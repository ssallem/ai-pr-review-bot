"""formatter 단위 테스트 — 순수 함수이므로 mock 없이 직접 호출."""

from __future__ import annotations

from datetime import date

from pr_describer.describer import PRDescription
from pr_describer.formatter import (
    format_changelog_entry,
    format_full_markdown,
    format_terminal,
)


def _desc(
    *,
    title: str = "feat(api): add endpoint",
    description: str = "## 변경 요약\n새 endpoint 추가\n\n## 주요 변경 사항\n- foo",
    changelog_entry: str = "API에 새 endpoint /v1/foo 추가",
    breaking_change: bool = False,
    type_: str = "feat",
    raw: str = "{...}",
) -> PRDescription:
    return PRDescription(
        title=title,
        description=description,
        changelog_entry=changelog_entry,
        breaking_change=breaking_change,
        type=type_,
        raw_response=raw,
    )


# ---------- format_full_markdown ---------------------------------------------


def test_format_full_markdown_basic_contains_description_sections() -> None:
    """일반 PRDescription → description 본문과 섹션 헤더가 포함되어야 한다."""
    desc = _desc()
    md = format_full_markdown(desc)

    # description의 마크다운 섹션 헤더가 그대로 보존되어야 함.
    assert "## 변경 요약" in md
    assert "## 주요 변경 사항" in md
    assert "새 endpoint 추가" in md
    # title은 format_full_markdown 본문에 포함하지 않음 (GitHub PR title은 별도 필드).
    assert "feat(api): add endpoint" not in md
    # Breaking 마커가 false일 때 등장하지 않아야 함.
    assert "Breaking change" not in md
    # 끝에는 개행이 있어야 GitHub에서 깔끔히 렌더됨.
    assert md.endswith("\n")


def test_format_full_markdown_breaking_change_adds_marker() -> None:
    """breaking_change=True → 마크다운에 BREAKING 마커 표시."""
    desc = _desc(breaking_change=True)
    md = format_full_markdown(desc)

    assert "Breaking change" in md
    # 본문도 함께 보존되어야 한다.
    assert "## 변경 요약" in md


# ---------- format_changelog_entry -------------------------------------------


def test_format_changelog_entry_uses_provided_date_and_korean_entry() -> None:
    """changelog 엔트리 포맷: `- YYYY-MM-DD (type): 한국어 entry`."""
    desc = _desc(changelog_entry="API에 새 endpoint /v1/foo 추가")
    line = format_changelog_entry(desc, today=date(2026, 5, 10))

    assert line.startswith("- 2026-05-10 (feat): API에 새 endpoint /v1/foo 추가")
    assert line.endswith("\n")
    # 마침표는 제거되어야 함 (rstrip(".")).
    assert not line.rstrip("\n").endswith(".")


def test_format_changelog_entry_marks_breaking() -> None:
    """breaking_change=True → type 옆에 ', breaking' 표기."""
    desc = _desc(breaking_change=True, type_="refactor")
    line = format_changelog_entry(desc, today=date(2026, 1, 1))

    assert "(refactor, breaking)" in line


def test_format_changelog_entry_strips_trailing_period() -> None:
    """changelog_entry 끝의 마침표는 자동 제거 (스타일 일관)."""
    desc = _desc(changelog_entry="설명입니다.")
    line = format_changelog_entry(desc, today=date(2026, 5, 10))

    assert line.rstrip("\n").endswith("설명입니다")


# ---------- format_terminal --------------------------------------------------


def test_format_terminal_with_color_includes_ansi_codes() -> None:
    """color=True → ANSI 코드가 출력에 포함되어야 한다."""
    desc = _desc()
    out = format_terminal(desc, color=True)

    # 최소 한 가지 ANSI 시퀀스 존재 (BOLD 또는 CYAN 등).
    assert "\x1b[" in out
    # 본문 요소 포함.
    assert desc.title in out
    assert desc.changelog_entry in out
    assert "feat" in out


def test_format_terminal_no_color_strips_ansi() -> None:
    """color=False → ANSI escape 없이 평문이어야 한다."""
    desc = _desc()
    out = format_terminal(desc, color=False)

    assert "\x1b[" not in out
    assert desc.title in out
    assert desc.changelog_entry in out


def test_format_terminal_breaking_marker_visible() -> None:
    """breaking_change=True → 'BREAKING' 표기가 들어가야 한다."""
    desc = _desc(breaking_change=True)
    out = format_terminal(desc, color=False)

    assert "BREAKING" in out
