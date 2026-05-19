"""CLI 통합 테스트. CliRunner로 옵션 조합 검증, 외부 API는 모두 mock."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from pr_reviewer import cli as cli_module
from pr_reviewer.cli import _parse_pr_spec, main
from pr_reviewer.diff_loader import DiffPayload, FileDiff
from pr_reviewer.reviewer import ReviewIssue, ReviewResult


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fake_diff() -> DiffPayload:
    return DiffPayload(
        files=[FileDiff("a.py", "python", 2, 1, "@@ -1 +1,2 @@\n+new\n")],
    )


@pytest.fixture
def fake_review_result_clean() -> ReviewResult:
    return ReviewResult(issues=[], summary="문제 없음", raw_response="")


@pytest.fixture
def fake_review_result_critical() -> ReviewResult:
    return ReviewResult(
        issues=[
            ReviewIssue(
                severity="critical",
                file="a.py",
                line=1,
                category="potential_bug",
                message="critical 이슈",
            )
        ],
        summary="critical 발견",
        raw_response="",
    )


def test_parse_pr_spec_valid():
    owner, repo, number = _parse_pr_spec("octocat/hello-world#42")
    assert owner == "octocat"
    assert repo == "hello-world"
    assert number == 42


def test_parse_pr_spec_invalid_raises():
    import click

    with pytest.raises(click.BadParameter):
        _parse_pr_spec("not-a-spec")


def test_help_works(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "pr-review" in result.output.lower() or "Usage" in result.output


def test_no_input_source_errors(runner):
    result = runner.invoke(main, [])
    assert result.exit_code != 0
    assert "정확히 하나" in result.output or "Usage" in result.output


def test_multiple_input_sources_error(runner, tmp_path):
    diff_file = tmp_path / "x.diff"
    diff_file.write_text("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n+y\n")
    result = runner.invoke(
        main,
        ["--from-file", str(diff_file), "--from-stdin"],
    )
    assert result.exit_code != 0


def test_post_without_pr_errors(runner, tmp_path):
    diff_file = tmp_path / "x.diff"
    diff_file.write_text("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n+y\n")
    result = runner.invoke(main, ["--from-file", str(diff_file), "--post"])
    assert result.exit_code != 0
    assert "--post" in result.output or "함께만" in result.output


def test_from_file_terminal_output(
    runner, monkeypatch, tmp_path, fake_diff, fake_review_result_clean
):
    diff_path = tmp_path / "changes.diff"
    diff_path.write_text(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+x\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli_module, "_parse_pr_spec", _parse_pr_spec)
    # diff_loader.load_from_file는 실제로 동작 (작은 diff). reviewer만 mock.
    monkeypatch.setattr(
        "pr_reviewer.reviewer.review_diff",
        AsyncMock(return_value=fake_review_result_clean),
    )

    result = runner.invoke(main, ["--from-file", str(diff_path), "--output", "terminal"])
    assert result.exit_code == 0, result.output
    assert "발견하지 못했" in result.output or "Claude" in result.output


def test_from_file_json_output(
    runner, monkeypatch, tmp_path, fake_review_result_critical
):
    diff_path = tmp_path / "changes.diff"
    diff_path.write_text(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+x\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "pr_reviewer.reviewer.review_diff",
        AsyncMock(return_value=fake_review_result_critical),
    )

    result = runner.invoke(main, ["--from-file", str(diff_path), "--output", "json"])
    # critical 있으면 exit code 1.
    assert result.exit_code == 1, result.output
    assert '"severity": "critical"' in result.output


def test_from_file_md_output(
    runner, monkeypatch, tmp_path, fake_review_result_clean
):
    diff_path = tmp_path / "changes.diff"
    diff_path.write_text(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+x\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "pr_reviewer.reviewer.review_diff",
        AsyncMock(return_value=fake_review_result_clean),
    )

    result = runner.invoke(main, ["--from-file", str(diff_path), "--output", "md"])
    assert result.exit_code == 0
    assert "# 🤖 Claude 코드 리뷰" in result.output


def test_from_stdin_input(runner, monkeypatch, fake_review_result_clean):
    diff_text = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+x\n"
    )
    monkeypatch.setattr(
        "pr_reviewer.reviewer.review_diff",
        AsyncMock(return_value=fake_review_result_clean),
    )

    result = runner.invoke(main, ["--from-stdin"], input=diff_text)
    assert result.exit_code == 0


def test_pr_option_with_post_calls_github(
    runner, monkeypatch, fake_diff, fake_review_result_clean
):
    monkeypatch.setattr(cli_module, "_parse_pr_spec", _parse_pr_spec)

    # settings.github_token 주입.
    fake_settings = MagicMock()
    fake_settings.github_token = "ghp_dummy"
    fake_settings.claude_model = "claude-test"
    monkeypatch.setattr("pr_reviewer.config.settings", fake_settings)

    monkeypatch.setattr(
        "pr_reviewer.diff_loader.load_from_github_pr",
        AsyncMock(return_value=fake_diff),
    )
    monkeypatch.setattr(
        "pr_reviewer.reviewer.review_diff",
        AsyncMock(return_value=fake_review_result_clean),
    )
    post_mock = AsyncMock(return_value={"html_url": "https://x.example/c/1"})
    monkeypatch.setattr("pr_reviewer.github_client.post_pr_comment", post_mock)

    result = runner.invoke(
        main, ["--pr", "octo/repo#7", "--post", "--output", "terminal"]
    )
    assert result.exit_code == 0, result.output
    post_mock.assert_awaited_once()
    args, _kwargs = post_mock.call_args
    assert args[0] == "octo"
    assert args[1] == "repo"
    assert args[2] == 7


def test_pr_option_without_token_errors(runner, monkeypatch):
    fake_settings = MagicMock()
    fake_settings.github_token = None
    fake_settings.claude_model = "claude-test"
    monkeypatch.setattr("pr_reviewer.config.settings", fake_settings)

    result = runner.invoke(main, ["--pr", "octo/repo#7"])
    assert result.exit_code != 0
    assert "GITHUB_TOKEN" in result.output


def test_post_failure_returns_error_code(
    runner, monkeypatch, fake_diff, fake_review_result_clean
):
    fake_settings = MagicMock()
    fake_settings.github_token = "ghp_dummy"
    fake_settings.claude_model = "claude-test"
    monkeypatch.setattr("pr_reviewer.config.settings", fake_settings)

    monkeypatch.setattr(
        "pr_reviewer.diff_loader.load_from_github_pr",
        AsyncMock(return_value=fake_diff),
    )
    monkeypatch.setattr(
        "pr_reviewer.reviewer.review_diff",
        AsyncMock(return_value=fake_review_result_clean),
    )
    from pr_reviewer.github_client import GitHubApiError

    monkeypatch.setattr(
        "pr_reviewer.github_client.post_pr_comment",
        AsyncMock(side_effect=GitHubApiError("rate limit", status_code=403)),
    )

    result = runner.invoke(main, ["--pr", "octo/repo#7", "--post"])
    assert result.exit_code == 3
    assert "댓글 작성 실패" in result.output


def test_truncated_diff_warning_emitted(runner, monkeypatch, fake_review_result_clean):
    truncated = DiffPayload(
        files=[FileDiff("a.py", "python", 1, 0, "+x")],
        truncated=True,
        raw_size_bytes=999_999,
    )
    monkeypatch.setattr(
        "pr_reviewer.diff_loader.load_from_stdin", lambda: truncated
    )
    monkeypatch.setattr(
        "pr_reviewer.reviewer.review_diff",
        AsyncMock(return_value=fake_review_result_clean),
    )
    result = runner.invoke(main, ["--from-stdin"], input="dummy")
    assert "절단" in result.output
