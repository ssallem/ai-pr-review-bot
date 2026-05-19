"""Claude API로 PR 설명·체인지로그 생성. 시스템 프롬프트는 prompt cache 대상."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

import anthropic

from pr_describer.commit_loader import CommitsPayload
from pr_describer.config import settings
from pr_describer.logging_utils import get_logger

logger = get_logger(__name__)

PRType = Literal["feat", "fix", "refactor", "test", "docs", "chore", "perf", "ci"]
_VALID_TYPES: tuple[str, ...] = (
    "feat",
    "fix",
    "refactor",
    "test",
    "docs",
    "chore",
    "perf",
    "ci",
)


@dataclass
class PRDescription:
    """describer 출력. raw_response는 디버깅/검증용 원문 보존."""

    title: str
    description: str
    changelog_entry: str
    breaking_change: bool
    type: str
    raw_response: str


# 시스템 프롬프트 — 1024+ 토큰 보장을 위해 풍부한 가이드/예시 포함.
# Anthropic prompt caching min 1024 tokens (sonnet 기준). 이 프롬프트는
# 한국어/영문 설명 + 다양한 예시 케이스로 캐시 hit을 유도한다.
_SYSTEM_PROMPT_BASE = """당신은 한국어와 영어를 모두 능숙하게 다루는 시니어 소프트웨어 엔지니어입니다.
GitHub Pull Request의 커밋 메시지와 실제 코드 diff를 입력으로 받아, 동료 리뷰어와
릴리스 노트 독자가 빠르게 변경의 본질을 파악할 수 있는 고품질의 PR title·description·
changelog entry를 생성하는 역할을 맡고 있습니다.

# 산출물 규격

다음 다섯 가지를 정확히 채워야 합니다.

1. **title**
   - 한 줄, 영문.
   - 컨벤셔널 커밋 형식 사용 여부는 사용자 옵션에 따라 다릅니다.
   - 컨벤셔널이면 `<type>: <subject>` 또는 `<type>(<scope>): <subject>` 형태.
   - 70자 이내 권장. PR 트래커에서 잘리지 않도록 짧게.
   - 동사는 명령형 현재형(use, add, fix, refactor 등) 사용.

2. **description** (한국어 마크다운)
   다음 다섯 섹션을 빠짐없이 포함하되, 내용이 없는 섹션은 "해당 없음"으로 명시.
   - `## 변경 요약` — 3~5줄. 무엇을, 왜 바꿨는지.
   - `## 주요 변경 사항` — bullet 리스트. 파일·모듈 단위로 구체적으로.
   - `## 영향도 / Breaking change` — 기존 사용자/호출부에 미치는 영향. Breaking이면 명시적으로 "Breaking change: ..."로 시작.
   - `## 테스트 전략` — 어떤 테스트가 추가됐는지 또는 어떻게 검증했는지.
   - `## 리뷰 포인트` — 리뷰어가 특히 봐줘야 할 부분 (성능, 보안, 엣지케이스 등).

3. **changelog_entry** (한국어, 한 줄)
   - 사용자(end-user) 관점에서 무엇이 달라지는지.
   - 내부 리팩토링·테스트 추가 등 사용자가 체감 못하는 변경은 `내부:` prefix 사용.
   - 마침표 없이 간결하게.

4. **breaking_change** (boolean)
   - 공개 API 변경, 동작 호환성 깨짐, 환경변수/설정 키 변경, 의존 버전 강제 상향 등이면 true.

5. **type** (enum)
   - feat, fix, refactor, test, docs, chore, perf, ci 중 하나.
   - 가장 비중이 큰 변경 분류.

# 추론 규칙 (매우 중요)

- 입력으로 주어진 **커밋 메시지**와 **실제 diff** 외의 정보는 추측·창작하지 않습니다.
- diff에 없는 파일·함수·동작을 description에 적지 않습니다.
- 커밋 메시지가 부실하더라도 diff의 hunk 헤더(@@ ... @@), 변경된 함수 시그니처, import 변화로부터 사실만 추출합니다.
- 빈 diff(메타 변경만)인 경우 "diff 부재 — 메타 변경" 등으로 명시.
- truncated diff 안내 문구가 있으면 description의 "리뷰 포인트"에 "diff 일부 절단됨, 전체 변경은 GitHub에서 확인 권장"을 포함합니다.

# 출력 포맷 (반드시 JSON 한 덩어리)

다음 키를 가진 단일 JSON 객체만 반환합니다. 코드펜스, 자연어 prefix/suffix 금지.

```json
{
  "title": "string",
  "description": "string (markdown)",
  "changelog_entry": "string",
  "breaking_change": false,
  "type": "feat|fix|refactor|test|docs|chore|perf|ci"
}
```

# 예시 (참고용)

## 예시 1: 신규 기능 (conventional)

입력 커밋: "add gzip compression to response middleware"
출력 title: "feat(http): add gzip compression to response middleware"
출력 type: "feat"
출력 changelog_entry: "HTTP 응답에 gzip 압축이 적용되어 트래픽이 감소합니다"
breaking_change: false

## 예시 2: 버그 수정 (free-form)

입력 커밋: "Fix race condition in cache invalidation"
출력 title: "Fix race condition in cache invalidation"
출력 type: "fix"
출력 changelog_entry: "캐시 무효화 시 발생하던 동시성 버그를 수정"
breaking_change: false

## 예시 3: API 시그니처 변경 (breaking)

입력 커밋: "rename `parseInput` to `parse_input` for PEP8"
출력 title: "refactor!: rename parseInput → parse_input"
출력 type: "refactor"
출력 changelog_entry: "Breaking: parseInput()이 parse_input()으로 변경"
breaking_change: true

## 예시 4: 내부 리팩토링

입력 커밋: "extract validators into separate module"
출력 changelog_entry: "내부: validator 모듈 분리"
출력 type: "refactor"

# 한국어 표기 규칙

- 외래어는 일관성 있게 표기 (예: 컴포넌트, 라이브러리, 인터페이스). 영문 그대로 둘 때는 백틱 또는 영문 그대로.
- 존댓말 사용 ("~합니다", "~됩니다"). 평어체 금지.
- 숫자 단위는 KB, MB, ms 등 영문 약어 그대로.

이상의 규격을 모두 준수해 단일 JSON으로 응답하세요.
"""


def _system_prompt(*, conventional: bool) -> list[dict[str, Any]]:
    """prompt cache용 시스템 프롬프트 블록.

    cache_control을 ephemeral로 설정해 동일 시스템 프롬프트의 반복 호출에서 token 절감.
    """
    mode_note = (
        "현재 모드: 컨벤셔널 커밋 형식 (title은 `<type>: ...` 또는 `<type>(<scope>): ...`)."
        if conventional
        else "현재 모드: free-form (title은 자연어 한 줄, type 분류는 그대로 enum 중 하나)."
    )
    full = f"{_SYSTEM_PROMPT_BASE}\n\n# 모드\n\n{mode_note}\n"
    return [
        {
            "type": "text",
            "text": full,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _build_user_message(payload: CommitsPayload) -> str:
    """커밋 + diff를 단일 user 메시지로 조립."""
    lines = [
        f"# Branch: {payload.head_branch} ← {payload.base_branch}",
        "",
        f"# Commits ({len(payload.commits)}건)",
        "",
    ]
    if not payload.commits:
        lines.append("(커밋 없음)")
    else:
        for c in payload.commits:
            lines.append(f"## {c.sha[:12]} — {c.subject}")
            if c.author:
                lines.append(f"author: {c.author}")
            if c.body:
                lines.append("")
                lines.append(c.body)
            lines.append("")

    lines.extend(
        [
            "",
            "# Diff",
            "",
            "```diff",
            payload.diff if payload.diff.strip() else "(diff 없음)",
            "```",
        ]
    )
    if payload.truncated:
        lines.append("\n> 주의: 위 diff는 100KB 초과로 일부 절단됨.")
    return "\n".join(lines)


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _extract_json(raw: str) -> dict[str, Any] | None:
    """raw 텍스트에서 가장 바깥쪽 JSON 객체를 추출. 실패 시 None."""
    raw = raw.strip()
    # 모델이 가끔 코드펜스로 감싸므로 제거.
    if raw.startswith("```"):
        # ```json ... ``` 또는 ``` ... ```
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _normalize_type(raw_type: Any) -> str:
    """모델 응답의 type 값을 화이트리스트로 정규화. 매칭 실패 시 'chore'."""
    if not isinstance(raw_type, str):
        return "chore"
    candidate = raw_type.strip().lower()
    if candidate in _VALID_TYPES:
        return candidate
    return "chore"


def _coerce_bool(value: Any) -> bool:
    """JSON에서 가끔 문자열 true/false로 오는 경우를 흡수."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return False


def _build_fallback(raw: str, conventional: bool) -> PRDescription:
    """JSON 파싱 실패 fallback — raw 응답을 description으로 보존."""
    title_prefix = "chore: " if conventional else ""
    return PRDescription(
        title=f"{title_prefix}auto-generated PR description",
        description=raw.strip() or "(빈 응답)",
        changelog_entry="내부: 자동 생성 실패 — 수동 확인 필요",
        breaking_change=False,
        type="chore",
        raw_response=raw,
    )


def _make_client() -> anthropic.AsyncAnthropic:
    """anthropic 비동기 클라이언트. 테스트는 monkeypatch로 교체."""
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _extract_text(response: Any) -> str:
    """anthropic Message 응답에서 텍스트 블록만 합쳐 추출."""
    parts: list[str] = []
    content = getattr(response, "content", None) or []
    for block in content:
        # SDK는 block.type/text 또는 dict 형태로 줄 수 있음.
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(text)
    return "".join(parts)


async def describe_pr(
    payload: CommitsPayload,
    *,
    model: str | None = None,
    conventional: bool = True,
    client: anthropic.AsyncAnthropic | None = None,
) -> PRDescription:
    """커밋/​diff에서 PR title·description·changelog를 생성.

    Args:
        payload: commit_loader가 반환한 CommitsPayload.
        model: 모델 ID 오버라이드. None이면 settings.claude_model 사용.
        conventional: True면 컨벤셔널 커밋 형식 강제, False면 free-form.
        client: 테스트용 주입. None이면 settings 기반 신규 생성.

    Returns:
        PRDescription. JSON 파싱이 실패해도 fallback으로 객체 반환.
    """
    chosen_model = model or settings.claude_model
    own_client = client is None
    client = client or _make_client()

    system_blocks = _system_prompt(conventional=conventional)
    user_message = _build_user_message(payload)

    logger.info(
        "describe_pr: model=%s conventional=%s commits=%d diff_bytes=%d",
        chosen_model,
        conventional,
        len(payload.commits),
        len(payload.diff.encode("utf-8")),
    )

    try:
        response = await client.messages.create(
            model=chosen_model,
            max_tokens=2000,
            system=system_blocks,
            messages=[{"role": "user", "content": user_message}],
        )
    finally:
        # 테스트 주입 클라이언트는 외부에서 정리 책임.
        if own_client:
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # pragma: no cover — defensive
                    logger.debug("client close failed", exc_info=True)

    raw = _extract_text(response)
    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning("JSON 파싱 실패 — fallback 사용")
        return _build_fallback(raw, conventional)

    title = str(parsed.get("title") or "").strip() or "auto-generated PR"
    description = str(parsed.get("description") or "").strip() or "(빈 description)"
    changelog_entry = str(parsed.get("changelog_entry") or "").strip() or "내부: 변경 자동 생성"
    return PRDescription(
        title=title,
        description=description,
        changelog_entry=changelog_entry,
        breaking_change=_coerce_bool(parsed.get("breaking_change")),
        type=_normalize_type(parsed.get("type")),
        raw_response=raw,
    )
