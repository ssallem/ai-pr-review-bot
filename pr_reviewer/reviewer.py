"""Claude API 호출 + JSON 파싱. prompt cache로 시스템 프롬프트 재사용."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from anthropic import AsyncAnthropic

from .config import settings
from .diff_loader import DiffPayload

logger = logging.getLogger(__name__)

# 시스템 프롬프트가 1024 토큰을 넘어야 prompt cache 효과가 있다 (Claude 정책).
# 한국어 안내 + 5가지 검토 관점 + 출력 스키마를 충분히 길게 작성.
SYSTEM_PROMPT = """당신은 시니어 코드 리뷰어입니다. 다음 5가지 관점에서 PR 변경 사항을 객관적으로 검토합니다.

1. **잠재 버그 (potential_bug)**
   - null/None/undefined 처리 누락
   - off-by-one, 경계 조건 (빈 리스트, 0, 음수, 매우 큰 수)
   - race condition, deadlock, 비동기 처리 미흡
   - 자원 누수 (파일/소켓/DB 커넥션 close 누락)
   - 예외 처리 누락 또는 광범위한 except로 진짜 에러를 숨기는 패턴

2. **보안 (security)**
   - SQL injection, NoSQL injection
   - XSS, CSRF, open redirect
   - 시크릿/토큰/API 키 하드코딩, 로그 노출
   - 입력 검증 누락 (특히 외부 입력)
   - 권한 체크 누락, IDOR

3. **스타일·네이밍 (style)**
   - 변수명·함수명·클래스명의 가독성·일관성
   - 매직 넘버, 매직 스트링
   - 중복 코드, 너무 긴 함수 (50줄 초과), 너무 깊은 중첩 (4단계 초과)
   - 주석 부재 또는 과도한 주석

4. **테스트 (test)**
   - 새 로직에 대한 테스트 커버리지 추정
   - 엣지 케이스 테스트 누락
   - 모킹 부재로 외부 의존이 들어간 테스트
   - 테스트 이름이 행위를 설명하지 않음

5. **영향도 (impact)**
   - 변경이 기존 동작에 미치는 영향
   - breaking change 여부 (API 시그니처 변경, 응답 포맷 변경, DB 스키마 변경)
   - 마이그레이션/롤백 전략 누락

# 검토 원칙

- **추측 금지**: 코드에서 확인되지 않은 동작을 단정하지 않는다.
- **정확한 인용**: 이슈 보고 시 파일명과 라인을 정확히 적는다 (diff hunk의 + 라인 기준).
- **건설적 제안**: 문제만 지적하지 말고 가능하면 `suggested_fix`에 개선 코드를 적는다.
- **언어**: 모든 메시지는 한국어로 작성한다.

# 출력 형식

반드시 다음 JSON 스키마를 따른다. 다른 텍스트는 절대 포함하지 않는다.

```json
{
  "issues": [
    {
      "severity": "critical | warning | suggestion",
      "file": "경로/파일명",
      "line": 42,
      "category": "potential_bug | security | style | test | impact",
      "message": "문제 설명 (한국어, 1~3문장)",
      "suggested_fix": "권장 수정 코드 또는 접근법 (선택)"
    }
  ],
  "summary": "전체 리뷰 한 단락 요약 (한국어, 3~5문장). 가장 중요한 발견과 머지 가능성을 언급."
}
```

# severity 기준

- **critical**: 즉시 머지를 막아야 하는 보안/데이터 손상/심각한 버그
- **warning**: 잠재적 문제, 머지 전 논의 필요
- **suggestion**: 코드 품질 개선 제안, 머지를 막지는 않음

빈 issues 배열도 유효하다 (문제 없을 시)."""


@dataclass
class ReviewIssue:
    severity: str
    file: str
    line: int | None
    category: str
    message: str
    suggested_fix: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "category": self.category,
            "message": self.message,
            "suggested_fix": self.suggested_fix,
        }


@dataclass
class ReviewResult:
    issues: list[ReviewIssue]
    summary: str
    raw_response: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "summary": self.summary,
            "warnings": self.warnings,
        }


def _build_user_message(diff: DiffPayload) -> str:
    """diff payload를 Claude에 전달할 텍스트로 직렬화."""
    lines: list[str] = []
    if diff.pr_meta:
        lines.append("# PR 메타")
        for k in ("title", "author", "base_ref", "head_ref", "html_url"):
            if diff.pr_meta.get(k):
                lines.append(f"- {k}: {diff.pr_meta[k]}")
        if diff.pr_meta.get("body"):
            lines.append(f"- body:\n{diff.pr_meta['body']}")
        lines.append("")

    if diff.notes:
        lines.append("# 처리 메모")
        for note in diff.notes:
            lines.append(f"- {note}")
        lines.append("")

    lines.append(f"# 변경된 파일 ({len(diff.files)}개)")
    for f in diff.files:
        lines.append(f"- {f.filename} (+{f.additions} / -{f.deletions}, {f.language})")
    lines.append("")

    lines.append("# diff (unified format)")
    for f in diff.files:
        lines.append(f"\n## {f.filename}")
        lines.append("```diff")
        lines.append(f.hunks)
        lines.append("```")

    lines.append("")
    lines.append("위 변경 사항을 시스템 프롬프트의 5가지 관점으로 검토하고 JSON으로 답변하라.")
    return "\n".join(lines)


def _strip_code_fence(text: str) -> str:
    """Claude가 ```json ... ``` 으로 감싸 응답할 때 fence를 벗긴다."""
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    return text.strip()


def _parse_response(raw: str) -> tuple[list[ReviewIssue], str, list[str]]:
    """Claude 응답을 ReviewIssue 리스트 + summary로 파싱.

    실패 시 fallback: raw 텍스트를 summary로 두고 issues는 빈 리스트.
    """
    warnings_list: list[str] = []
    candidate = _strip_code_fence(raw)

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        logger.warning("JSON 파싱 실패, raw 텍스트 fallback: %s", exc)
        warnings_list.append(f"JSON 파싱 실패 ({exc.msg}). 원본 응답을 summary로 저장.")
        return [], raw, warnings_list

    if not isinstance(parsed, dict):
        warnings_list.append("응답이 JSON 객체가 아님. raw 텍스트로 fallback.")
        return [], raw, warnings_list

    raw_issues = parsed.get("issues") or []
    if not isinstance(raw_issues, list):
        warnings_list.append("issues가 배열이 아님. 빈 배열로 처리.")
        raw_issues = []

    issues: list[ReviewIssue] = []
    for idx, item in enumerate(raw_issues):
        if not isinstance(item, dict):
            warnings_list.append(f"issues[{idx}]가 객체가 아님. 건너뜀.")
            continue
        try:
            issues.append(
                ReviewIssue(
                    severity=str(item.get("severity", "suggestion")),
                    file=str(item.get("file", "")),
                    line=item.get("line") if isinstance(item.get("line"), int) else None,
                    category=str(item.get("category", "style")),
                    message=str(item.get("message", "")),
                    suggested_fix=item.get("suggested_fix"),
                )
            )
        except (TypeError, ValueError) as exc:
            warnings_list.append(f"issues[{idx}] 파싱 실패: {exc}. 건너뜀.")

    summary = str(parsed.get("summary") or "")
    return issues, summary, warnings_list


async def review_diff(
    diff: DiffPayload,
    *,
    model: str | None = None,
    client: AsyncAnthropic | None = None,
) -> ReviewResult:
    """diff를 Claude에 보내 코드 리뷰 결과를 받는다.

    Args:
        diff: 리뷰 대상 DiffPayload.
        model: 모델 ID 오버라이드. None이면 settings.claude_model.
        client: 테스트용 AsyncAnthropic 주입. None이면 settings 기반 신규 생성.
    """
    if not diff.files:
        return ReviewResult(
            issues=[],
            summary="변경된 파일이 없어 리뷰를 건너뜀.",
            raw_response="",
            warnings=["empty diff"],
        )

    use_model = model or settings.claude_model
    use_client = client or AsyncAnthropic(api_key=settings.anthropic_api_key)

    user_message = _build_user_message(diff)

    response = await use_client.messages.create(
        model=use_model,
        max_tokens=4000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = _extract_text(response)
    issues, summary, parse_warnings = _parse_response(raw_text)

    warnings_list = list(parse_warnings)
    if diff.truncated:
        warnings_list.append("입력 diff가 절단됨 — 일부 변경이 리뷰에서 누락되었을 수 있음")

    return ReviewResult(
        issues=issues,
        summary=summary or raw_text,
        raw_response=raw_text,
        warnings=warnings_list,
    )


def _extract_text(response: Any) -> str:
    """anthropic 응답 객체에서 텍스트 컨텐츠 추출. mock 호환."""
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if not content:
        return ""

    parts: list[str] = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type is None and isinstance(block, dict):
            block_type = block.get("type")
        text_value = getattr(block, "text", None)
        if text_value is None and isinstance(block, dict):
            text_value = block.get("text")
        if block_type == "text" and text_value:
            parts.append(text_value)
    return "".join(parts)
