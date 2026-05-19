"""formatter 단위 테스트."""

from __future__ import annotations

import json

from pr_reviewer.formatter import format_as_json, format_as_markdown, format_as_terminal
from pr_reviewer.reviewer import ReviewIssue, ReviewResult


def _result_with_two_issues() -> ReviewResult:
    return ReviewResult(
        issues=[
            ReviewIssue(
                severity="critical",
                file="src/foo.py",
                line=4,
                category="potential_bug",
                message="x가 None일 가능성",
                suggested_fix="if x is None: return None",
            ),
            ReviewIssue(
                severity="suggestion",
                file="src/bar.js",
                line=11,
                category="style",
                message="디버그 로그 제거 필요",
            ),
        ],
        summary="critical 1건, suggestion 1건",
        raw_response="raw",
    )


def test_format_as_markdown_groups_severity():
    result = _result_with_two_issues()
    md = format_as_markdown(result)
    assert "Claude 코드 리뷰" in md
    assert "Critical" in md
    assert "Suggestion" in md
    assert "src/foo.py:4" in md
    assert "src/bar.js:11" in md
    assert "x가 None" in md
    assert "if x is None" in md
    assert "## 요약" in md
    assert "critical 1건" in md


def test_format_as_markdown_no_issues():
    result = ReviewResult(issues=[], summary="문제 없음", raw_response="")
    md = format_as_markdown(result)
    assert "발견하지 못했" in md
    assert "문제 없음" in md


def test_format_as_markdown_includes_warnings():
    result = ReviewResult(
        issues=[],
        summary="ok",
        raw_response="",
        warnings=["입력 절단됨"],
    )
    md = format_as_markdown(result)
    assert "처리 경고" in md
    assert "입력 절단됨" in md


def test_format_as_terminal_contains_text():
    result = _result_with_two_issues()
    out = format_as_terminal(result)
    # ANSI 컬러 코드를 stripping 하지 않고 substring 검사.
    assert "Claude 코드 리뷰" in out
    assert "src/foo.py:4" in out
    assert "잠재 버그" in out
    assert "디버그 로그 제거" in out


def test_format_as_terminal_no_issues():
    result = ReviewResult(issues=[], summary="ok", raw_response="")
    out = format_as_terminal(result)
    assert "발견하지 못했" in out


def test_format_as_terminal_with_warnings():
    result = ReviewResult(issues=[], summary="ok", raw_response="", warnings=["w1"])
    out = format_as_terminal(result)
    assert "w1" in out


def test_format_as_json_round_trip():
    result = _result_with_two_issues()
    out = format_as_json(result)
    parsed = json.loads(out)
    assert isinstance(parsed["issues"], list)
    assert len(parsed["issues"]) == 2
    assert parsed["issues"][0]["severity"] == "critical"
    assert parsed["summary"] == "critical 1건, suggestion 1건"


def test_unknown_severity_falls_back_to_suggestion_group():
    weird = ReviewResult(
        issues=[
            ReviewIssue(
                severity="unknown",
                file="a",
                line=None,
                category="style",
                message="msg",
            )
        ],
        summary="s",
        raw_response="",
    )
    md = format_as_markdown(weird)
    # unknown → suggestion 버킷으로 처리되어 출력에 들어감.
    assert "msg" in md
