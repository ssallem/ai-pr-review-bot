"""cli 단위 테스트 — click.testing.CliRunner + 모듈 함수 mock.

`pr_describer.cli` 가 상단에서 import한 함수들을 monkeypatch로 교체해서
실제 git/GitHub/Anthropic 호출 없이 main 진입점을 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from pr_describer import cli as cli_module
from pr_describer.commit_loader import CommitsPayload
from pr_describer.describer import PRDescription


# ---------- 공용 fixture ------------------------------------------------------


def _fake_desc() -> PRDescription:
    return PRDescription(
        title="feat: do thing",
        description="## 변경 요약\n그 일을 함",
        changelog_entry="새 기능 추가",
        breaking_change=False,
        type="feat",
        raw_response="{...}",
    )


def _fake_payload() -> CommitsPayload:
    return CommitsPayload(
        commits=[],
        diff="diff --git a/x b/x\n+y\n",
        base_branch="main",
        head_branch="HEAD",
        truncated=False,
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------- 테스트 -----------------------------------------------------------


def test_cli_help_lists_known_options(runner: CliRunner) -> None:
    """--help 출력에 주요 옵션이 모두 노출되어야 한다."""
    result = runner.invoke(cli_module.main, ["--help"])

    assert result.exit_code == 0
    out = result.output
    assert "--base" in out
    assert "--head" in out
    assert "--from-github" in out
    assert "--update" in out
    assert "--output" in out
    assert "--no-conventional" in out


def test_cli_from_github_invokes_loader_and_describer(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    """--from-github OWNER/REPO#N → load_from_github_pr가 호출되고 결과 출력."""
    call_log: dict[str, Any] = {}

    async def fake_load_from_github_pr(
        owner: str, repo: str, pr_number: int, token: str, **kwargs: Any
    ) -> CommitsPayload:
        call_log["github"] = (owner, repo, pr_number, token)
        return _fake_payload()

    async def fake_describe_pr(payload: CommitsPayload, **kwargs: Any) -> PRDescription:
        call_log["describe"] = kwargs
        return _fake_desc()

    monkeypatch.setattr(cli_module, "load_from_github_pr", fake_load_from_github_pr)
    monkeypatch.setattr(cli_module, "describe_pr", fake_describe_pr)

    result = runner.invoke(
        cli_module.main,
        ["--from-github", "octo/repo#42", "--output", "terminal", "--no-color"],
    )

    assert result.exit_code == 0, result.output
    assert call_log["github"] == ("octo", "repo", 42, "ghp-test-dummy")
    # terminal 출력에 title이 포함되어야 함.
    assert "feat: do thing" in result.output


def test_cli_output_json_emits_serialized_description(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    """--output json → PRDescription이 JSON으로 직렬화되어 stdout으로 나간다."""

    def fake_load_from_git(*, base: str, head: str, **kwargs: Any) -> CommitsPayload:
        return _fake_payload()

    async def fake_describe_pr(payload: CommitsPayload, **kwargs: Any) -> PRDescription:
        return _fake_desc()

    monkeypatch.setattr(cli_module, "load_from_git", fake_load_from_git)
    monkeypatch.setattr(cli_module, "describe_pr", fake_describe_pr)

    result = runner.invoke(cli_module.main, ["--output", "json"])

    assert result.exit_code == 0, result.output
    # stdout에서 JSON 한 덩어리 파싱 가능해야 함.
    # (stderr에 경고가 갈 수도 있으나 stdout은 순수 JSON이어야 함.)
    payload = json.loads(result.output)
    assert payload["title"] == "feat: do thing"
    assert payload["type"] == "feat"
    assert payload["breaking_change"] is False
    assert "changelog_entry" in payload


def test_cli_update_calls_github_client_update_pr(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    """--update → github_client.update_pr가 인자와 함께 호출되어야 한다."""
    update_calls: list[dict[str, Any]] = []

    def fake_load_from_git(**kwargs: Any) -> CommitsPayload:
        return _fake_payload()

    async def fake_describe_pr(payload: CommitsPayload, **kwargs: Any) -> PRDescription:
        return _fake_desc()

    async def fake_update_pr(
        owner: str,
        repo: str,
        pr_number: int,
        title: str,
        body: str,
        token: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        update_calls.append(
            {
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "title": title,
                "body": body,
                "token": token,
            }
        )
        return {"number": pr_number}

    monkeypatch.setattr(cli_module, "load_from_git", fake_load_from_git)
    monkeypatch.setattr(cli_module, "describe_pr", fake_describe_pr)
    monkeypatch.setattr(cli_module, "update_pr", fake_update_pr)

    result = runner.invoke(
        cli_module.main,
        ["--update", "octo/repo#11", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    assert len(update_calls) == 1
    c = update_calls[0]
    assert c["owner"] == "octo"
    assert c["repo"] == "repo"
    assert c["pr_number"] == 11
    assert c["title"] == "feat: do thing"
    assert c["token"] == "ghp-test-dummy"


def test_cli_invalid_pr_ref_returns_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    """잘못된 PR ref 형식 → click이 사용법 오류를 내며 0이 아닌 exit code."""

    # load_from_git 미호출 보장 — 어차피 ref parse에서 막혀야 함.
    def fake_load_from_git(**kwargs: Any) -> CommitsPayload:  # pragma: no cover
        raise AssertionError("should not be called")

    monkeypatch.setattr(cli_module, "load_from_git", fake_load_from_git)

    result = runner.invoke(cli_module.main, ["--from-github", "not-a-valid-ref"])

    # click이 BadParameter를 UsageError로 처리해 0이 아닌 exit code를 반환.
    # 정확한 메시지 포맷은 click 버전에 따라 다르므로 exit code만 검증.
    assert result.exit_code != 0


def test_cli_prepend_changelog_writes_to_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner: CliRunner
) -> None:
    """--prepend-changelog → 현재 디렉토리(CliRunner의 isolated cwd)에 CHANGELOG.md 작성."""

    def fake_load_from_git(**kwargs: Any) -> CommitsPayload:
        return _fake_payload()

    async def fake_describe_pr(payload: CommitsPayload, **kwargs: Any) -> PRDescription:
        return _fake_desc()

    monkeypatch.setattr(cli_module, "load_from_git", fake_load_from_git)
    monkeypatch.setattr(cli_module, "describe_pr", fake_describe_pr)

    with runner.isolated_filesystem():
        result = runner.invoke(
            cli_module.main, ["--prepend-changelog", "--output", "json"]
        )
        assert result.exit_code == 0, result.output
        changelog = Path("CHANGELOG.md")
        assert changelog.exists()
        text = changelog.read_text(encoding="utf-8")
        assert "새 기능 추가" in text
