"""PRDescription을 사람이 읽거나 GitHub/CHANGELOG에 쓸 형태로 변환."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from pr_describer.describer import PRDescription

# 터미널 ANSI 컬러 — 외부 라이브러리 없이 최소한.
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_RED = "\x1b[31m"
_DIM = "\x1b[2m"


def format_full_markdown(desc: "PRDescription") -> str:
    """GitHub PR body에 그대로 붙여넣을 마크다운.

    title은 description 본문에 포함시키지 않음 — GitHub는 title을 별도 필드로 받기 때문.
    """
    bc_marker = (
        "\n\n> **Breaking change**: 본 PR은 호환성을 깨뜨리는 변경을 포함합니다.\n"
        if desc.breaking_change
        else ""
    )
    body = desc.description.rstrip()
    if not body.endswith("\n"):
        body += "\n"
    return f"{body}{bc_marker}"


def format_changelog_entry(
    desc: "PRDescription", *, today: date | None = None
) -> str:
    """CHANGELOG.md 맨 위에 prepend할 한 줄.

    포맷: `- YYYY-MM-DD (type): entry` — breaking은 `(type, breaking)` 표기.
    """
    today = today or date.today()
    type_label = f"{desc.type}, breaking" if desc.breaking_change else desc.type
    entry = desc.changelog_entry.strip().rstrip(".")
    return f"- {today.isoformat()} ({type_label}): {entry}\n"


def format_terminal(desc: "PRDescription", *, color: bool = True) -> str:
    """사람이 터미널에서 보기 좋게 — 색은 옵션."""

    def c(code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if color else text

    bc_line = (
        c(_RED + _BOLD, "BREAKING CHANGE") if desc.breaking_change else c(_GREEN, "no breaking")
    )
    type_line = c(_CYAN, desc.type)

    lines = [
        c(_BOLD, "── PR Title ────────────────────────────"),
        desc.title,
        "",
        c(_BOLD, "── Meta ───────────────────────────────"),
        f"type: {type_line}    impact: {bc_line}",
        "",
        c(_BOLD, "── Description ────────────────────────"),
        desc.description.rstrip(),
        "",
        c(_BOLD, "── Changelog Entry ────────────────────"),
        c(_YELLOW, desc.changelog_entry),
        "",
        c(_DIM, f"(model raw response length: {len(desc.raw_response)} chars)"),
    ]
    return "\n".join(lines) + "\n"
