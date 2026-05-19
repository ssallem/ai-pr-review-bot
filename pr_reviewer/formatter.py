"""ReviewResult를 마크다운/터미널/JSON으로 직렬화."""

from __future__ import annotations

import json

import click

from .reviewer import ReviewIssue, ReviewResult

_SEVERITY_ICON = {
    "critical": "🔴",
    "warning": "🟡",
    "suggestion": "🟢",
}

_SEVERITY_ORDER = ["critical", "warning", "suggestion"]

_SEVERITY_COLOR = {
    "critical": "red",
    "warning": "yellow",
    "suggestion": "green",
}

_CATEGORY_LABEL_KO = {
    "potential_bug": "잠재 버그",
    "security": "보안",
    "style": "스타일",
    "test": "테스트",
    "impact": "영향도",
}


def _group_by_severity(issues: list[ReviewIssue]) -> dict[str, list[ReviewIssue]]:
    grouped: dict[str, list[ReviewIssue]] = {sev: [] for sev in _SEVERITY_ORDER}
    for issue in issues:
        sev = issue.severity if issue.severity in grouped else "suggestion"
        grouped[sev].append(issue)
    return grouped


def _format_issue_md(issue: ReviewIssue) -> str:
    location = issue.file
    if issue.line is not None:
        location = f"{issue.file}:{issue.line}"
    category_ko = _CATEGORY_LABEL_KO.get(issue.category, issue.category)

    parts = [f"- **`{location}`** ({category_ko}) — {issue.message}"]
    if issue.suggested_fix:
        parts.append("")
        parts.append("  ```")
        for line in issue.suggested_fix.splitlines() or [issue.suggested_fix]:
            parts.append(f"  {line}")
        parts.append("  ```")
    return "\n".join(parts)


def format_as_markdown(result: ReviewResult) -> str:
    """PR 댓글용 마크다운."""
    lines: list[str] = []
    lines.append("# 🤖 Claude 코드 리뷰")
    lines.append("")

    grouped = _group_by_severity(result.issues)
    total = sum(len(v) for v in grouped.values())

    if total == 0:
        lines.append("문제를 발견하지 못했습니다. ✅")
    else:
        for sev in _SEVERITY_ORDER:
            items = grouped[sev]
            if not items:
                continue
            icon = _SEVERITY_ICON[sev]
            lines.append(f"## {icon} {sev.capitalize()} ({len(items)}건)")
            lines.append("")
            for issue in items:
                lines.append(_format_issue_md(issue))
                lines.append("")

    lines.append("## 요약")
    lines.append("")
    lines.append(result.summary or "(요약 없음)")

    if result.warnings:
        lines.append("")
        lines.append("---")
        lines.append("> ⚠️ 처리 경고:")
        for w in result.warnings:
            lines.append(f"> - {w}")

    return "\n".join(lines).rstrip() + "\n"


def format_as_terminal(result: ReviewResult) -> str:
    """터미널 컬러 출력 (click.style 사용)."""
    lines: list[str] = []
    title = click.style("=== Claude 코드 리뷰 ===", bold=True, fg="cyan")
    lines.append(title)
    lines.append("")

    grouped = _group_by_severity(result.issues)
    total = sum(len(v) for v in grouped.values())

    if total == 0:
        lines.append(click.style("문제를 발견하지 못했습니다.", fg="green"))
    else:
        for sev in _SEVERITY_ORDER:
            items = grouped[sev]
            if not items:
                continue
            color = _SEVERITY_COLOR[sev]
            icon = _SEVERITY_ICON[sev]
            header = click.style(
                f"[{icon} {sev.upper()}] {len(items)}건",
                fg=color,
                bold=True,
            )
            lines.append(header)
            for issue in items:
                location = issue.file
                if issue.line is not None:
                    location = f"{issue.file}:{issue.line}"
                category_ko = _CATEGORY_LABEL_KO.get(issue.category, issue.category)
                loc_styled = click.style(location, fg=color)
                lines.append(f"  - {loc_styled} ({category_ko})")
                lines.append(f"      {issue.message}")
                if issue.suggested_fix:
                    fix_label = click.style("    suggested:", dim=True)
                    lines.append(fix_label)
                    for fix_line in issue.suggested_fix.splitlines() or [issue.suggested_fix]:
                        lines.append(f"      {fix_line}")
            lines.append("")

    lines.append(click.style("--- 요약 ---", bold=True))
    lines.append(result.summary or "(요약 없음)")

    if result.warnings:
        lines.append("")
        lines.append(click.style("처리 경고:", fg="yellow"))
        for w in result.warnings:
            lines.append(f"  - {w}")

    return "\n".join(lines)


def format_as_json(result: ReviewResult) -> str:
    """기계 처리용 JSON 출력."""
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
