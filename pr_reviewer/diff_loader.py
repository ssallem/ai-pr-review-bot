"""diff 입력 소스 어댑터. 파일/stdin/GitHub API 세 경로를 같은 DiffPayload로 정규화."""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Claude 컨텍스트 비용 보호용 임계값 (100KB).
MAX_DIFF_BYTES = 100 * 1024
MAX_FILE_HUNK_BYTES = 20 * 1024

# unified diff 헤더 패턴: "diff --git a/foo b/foo".
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)

_LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".sh": "bash",
    ".sql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".md": "markdown",
}


@dataclass
class FileDiff:
    """단일 파일의 변경 사항."""

    filename: str
    language: str
    additions: int
    deletions: int
    hunks: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "language": self.language,
            "additions": self.additions,
            "deletions": self.deletions,
            "hunks": self.hunks,
        }


@dataclass
class DiffPayload:
    """Claude에 전달할 diff + 부가 메타."""

    files: list[FileDiff]
    pr_meta: dict[str, Any] | None = None
    truncated: bool = False
    raw_size_bytes: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def total_additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def total_deletions(self) -> int:
        return sum(f.deletions for f in self.files)


def _detect_language(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return _LANGUAGE_BY_EXT.get(ext, "text")


def _count_changes(hunk_text: str) -> tuple[int, int]:
    """+/-로 시작하는 라인 수를 센다 (헤더 제외)."""
    additions = 0
    deletions = 0
    for line in hunk_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def _split_into_files(diff_text: str) -> list[tuple[str, str]]:
    """unified diff을 파일 단위로 분리. 반환 [(filename, file_diff_text), ...]."""
    matches = list(_DIFF_HEADER_RE.finditer(diff_text))
    if not matches:
        return []

    results: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(diff_text)
        # b/ 경로(target)를 파일명으로 사용.
        filename = match.group(2)
        results.append((filename, diff_text[start:end]))
    return results


def _build_payload(diff_text: str, *, pr_meta: dict[str, Any] | None = None) -> DiffPayload:
    raw_size = len(diff_text.encode("utf-8"))
    notes: list[str] = []
    truncated_overall = False

    file_chunks = _split_into_files(diff_text)
    files: list[FileDiff] = []

    for filename, chunk in file_chunks:
        chunk_for_use = chunk
        if len(chunk.encode("utf-8")) > MAX_FILE_HUNK_BYTES:
            # 비용 보호: 파일 단위로 hunk truncate.
            truncated_overall = True
            chunk_for_use = chunk.encode("utf-8")[:MAX_FILE_HUNK_BYTES].decode(
                "utf-8", errors="ignore"
            )
            chunk_for_use += f"\n... [truncated: 원본 {len(chunk)} bytes]\n"
            notes.append(f"{filename}: hunk 길이 초과로 일부 절단됨")

        additions, deletions = _count_changes(chunk_for_use)
        files.append(
            FileDiff(
                filename=filename,
                language=_detect_language(filename),
                additions=additions,
                deletions=deletions,
                hunks=chunk_for_use,
            )
        )

    if raw_size > MAX_DIFF_BYTES:
        truncated_overall = True
        notes.append(
            f"전체 diff 크기 {raw_size} bytes가 한계({MAX_DIFF_BYTES})를 초과해 파일 단위 절단 적용됨"
        )

    return DiffPayload(
        files=files,
        pr_meta=pr_meta,
        truncated=truncated_overall,
        raw_size_bytes=raw_size,
        notes=notes,
    )


def load_from_file(path: str) -> DiffPayload:
    """로컬 unified diff 파일에서 로드."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"diff 파일을 찾을 수 없음: {path}")
    diff_text = file_path.read_text(encoding="utf-8", errors="replace")
    return _build_payload(diff_text)


def load_from_stdin() -> DiffPayload:
    """stdin에서 unified diff 읽기 (`git diff | pr-review --from-stdin`)."""
    diff_text = sys.stdin.read()
    if not diff_text.strip():
        raise ValueError("stdin이 비어있음")
    return _build_payload(diff_text)


async def load_from_github_pr(
    owner: str,
    repo: str,
    pr_number: int,
    github_token: str,
    *,
    timeout: float = 30.0,
) -> DiffPayload:
    """GitHub PR API로 diff + 메타 조회.

    1) PR 메타: `/repos/{owner}/{repo}/pulls/{pr_number}` (Accept: application/vnd.github+json)
    2) PR diff: 같은 endpoint에 Accept: application/vnd.github.v3.diff
    """
    if not github_token:
        raise ValueError("GitHub PR 로드는 github_token 필수")

    base_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    auth_headers = {
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        meta_resp = await client.get(
            base_url,
            headers={**auth_headers, "Accept": "application/vnd.github+json"},
        )
        meta_resp.raise_for_status()
        meta_json = meta_resp.json()

        diff_resp = await client.get(
            base_url,
            headers={**auth_headers, "Accept": "application/vnd.github.v3.diff"},
        )
        diff_resp.raise_for_status()
        diff_text = diff_resp.text

    pr_meta = {
        "title": meta_json.get("title"),
        "body": meta_json.get("body"),
        "author": (meta_json.get("user") or {}).get("login"),
        "base_ref": (meta_json.get("base") or {}).get("ref"),
        "head_ref": (meta_json.get("head") or {}).get("ref"),
        "html_url": meta_json.get("html_url"),
        "owner": owner,
        "repo": repo,
        "pr_number": pr_number,
    }
    return _build_payload(diff_text, pr_meta=pr_meta)
