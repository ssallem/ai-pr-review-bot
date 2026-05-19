"""commit_loader 단위 테스트 — git subprocess + GitHub API 모킹."""

from __future__ import annotations

import subprocess
from typing import Any

import httpx
import pytest

from pr_describer import commit_loader
from pr_describer.commit_loader import (
    MAX_DIFF_BYTES,
    Commit,
    CommitLoaderError,
    CommitsPayload,
    _parse_git_log,
    _truncate_diff,
    load_from_git,
    load_from_github_pr,
)

# ---------- _parse_git_log ---------------------------------------------------


def _make_record(sha: str, subject: str, body: str, author: str) -> str:
    fs = "\x1f"
    rs = "\x1e"
    return f"{sha}{fs}{subject}{fs}{body}{fs}{author}{rs}"


def test_parse_git_log_single_commit() -> None:
    raw = _make_record("abc123", "feat: add x", "본문 줄", "Alice")
    commits = _parse_git_log(raw)
    assert commits == [Commit(sha="abc123", subject="feat: add x", body="본문 줄", author="Alice")]


def test_parse_git_log_multiple_commits() -> None:
    raw = _make_record("a1", "s1", "", "u1") + _make_record("b2", "s2", "body2", "u2")
    commits = _parse_git_log(raw)
    assert len(commits) == 2
    assert commits[0].sha == "a1"
    assert commits[1].body == "body2"


def test_parse_git_log_empty_input() -> None:
    assert _parse_git_log("") == []
    assert _parse_git_log("\n\n") == []


def test_parse_git_log_skips_malformed_record() -> None:
    # 필드 4개 미만 — skip.
    bad = "onlyone\x1e"
    good = _make_record("c3", "ok", "", "u3")
    commits = _parse_git_log(bad + good)
    assert len(commits) == 1
    assert commits[0].sha == "c3"


# ---------- _truncate_diff ---------------------------------------------------


def test_truncate_diff_under_limit() -> None:
    diff = "small diff"
    result, truncated = _truncate_diff(diff)
    assert result == diff
    assert truncated is False


def test_truncate_diff_over_limit() -> None:
    diff = "a" * (MAX_DIFF_BYTES + 100)
    result, truncated = _truncate_diff(diff)
    assert truncated is True
    assert "truncated" in result
    # 잘린 본문은 한도보다 작거나 같음 + 안내 문구.
    assert len(result.encode("utf-8")) < len(diff.encode("utf-8"))


# ---------- load_from_git ----------------------------------------------------


class _FakeCompleted:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_load_from_git_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    log_record = _make_record("d4", "fix: bug", "본문", "Bob")
    log_record += _make_record("e5", "feat: new", "", "Carol")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        calls.append(cmd)
        if cmd[1:3] == ["rev-parse", "--is-inside-work-tree"]:
            return _FakeCompleted("true\n")
        if cmd[1] == "log":
            return _FakeCompleted(log_record)
        if cmd[1] == "diff":
            return _FakeCompleted("diff --git a/x b/x\n+added\n")
        raise AssertionError(f"unexpected cmd {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    payload = load_from_git(base="main", head="HEAD")
    assert isinstance(payload, CommitsPayload)
    assert payload.base_branch == "main"
    assert payload.head_branch == "HEAD"
    assert len(payload.commits) == 2
    assert payload.truncated is False
    # git이 3번 호출됐는지 (rev-parse, log, diff).
    assert len(calls) == 3


def test_load_from_git_not_a_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        return _FakeCompleted("false\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(CommitLoaderError, match="git 저장소가 아닙니다"):
        load_from_git()


def test_load_from_git_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(CommitLoaderError, match="git 실행 파일"):
        load_from_git()


def test_load_from_git_log_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        if cmd[1:3] == ["rev-parse", "--is-inside-work-tree"]:
            return _FakeCompleted("true\n")
        # log/diff 단계에서 git이 실패한 것처럼.
        raise subprocess.CalledProcessError(
            returncode=128, cmd=cmd, output="", stderr="bad revision"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(CommitLoaderError, match="bad revision"):
        load_from_git(base="nonexistent")


def test_load_from_git_truncates_large_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    big_diff = "x" * (MAX_DIFF_BYTES + 50)

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        if cmd[1:3] == ["rev-parse", "--is-inside-work-tree"]:
            return _FakeCompleted("true\n")
        if cmd[1] == "log":
            return _FakeCompleted(_make_record("f6", "subj", "", "u"))
        if cmd[1] == "diff":
            return _FakeCompleted(big_diff)
        raise AssertionError(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    payload = load_from_git()
    assert payload.truncated is True
    assert "truncated" in payload.diff


# ---------- load_from_github_pr ---------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            raise httpx.HTTPStatusError(
                "err",
                request=httpx.Request("GET", "https://example"),
                response=httpx.Response(self.status_code, text=self.text),
            )


class _FakeAsyncClient:
    """async with 컨텍스트 없이 단순 get만 mock."""

    def __init__(self, responses: dict[tuple[str, str], _FakeResponse]) -> None:
        # key: (method, accept_header_marker) → 응답
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        headers = headers or {}
        accept = headers.get("Accept", "")
        marker = "diff" if "diff" in accept else "json"
        self.calls.append(("GET", url, headers))
        if (url, marker) in self.responses:
            return self.responses[(url, marker)]
        # 폴백: url만으로 매칭.
        for (k_url, k_marker), v in self.responses.items():
            if k_url == url and k_marker == marker:
                return v
        raise AssertionError(f"no mock for {url} ({marker})")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_load_from_github_pr_happy_path() -> None:
    pr_url = "https://api.github.com/repos/o/r/pulls/7"
    commits_url = pr_url + "/commits"
    responses = {
        (pr_url, "json"): _FakeResponse(
            json_data={
                "base": {"ref": "main"},
                "head": {"ref": "feature/x"},
            }
        ),
        (commits_url, "json"): _FakeResponse(
            json_data=[
                {
                    "sha": "aaa",
                    "commit": {
                        "message": "feat: add x\n\n본문 1줄\n둘째 줄",
                        "author": {"name": "Alice"},
                    },
                    "author": {"login": "alice"},
                }
            ]
        ),
        (pr_url, "diff"): _FakeResponse(text="diff --git a/x b/x\n+y\n"),
    }
    fake_client = _FakeAsyncClient(responses)
    payload = await load_from_github_pr(
        "o", "r", 7, github_token="t", client=fake_client  # type: ignore[arg-type]
    )
    assert payload.base_branch == "main"
    assert payload.head_branch == "feature/x"
    assert len(payload.commits) == 1
    assert payload.commits[0].subject == "feat: add x"
    assert "본문 1줄" in payload.commits[0].body
    assert payload.commits[0].author == "Alice"
    assert payload.truncated is False
    # 3개 GET 호출 (PR meta + commits + diff).
    assert len(fake_client.calls) == 3


@pytest.mark.asyncio
async def test_load_from_github_pr_http_error() -> None:
    pr_url = "https://api.github.com/repos/o/r/pulls/9"
    responses = {(pr_url, "json"): _FakeResponse(status_code=404, text="Not Found")}
    fake_client = _FakeAsyncClient(responses)
    with pytest.raises(CommitLoaderError, match="404"):
        await load_from_github_pr(
            "o", "r", 9, github_token="t", client=fake_client  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_load_from_github_pr_network_error() -> None:
    class _BoomClient:
        async def get(self, *args: Any, **kwargs: Any) -> Any:
            raise httpx.ConnectError("boom")

        async def aclose(self) -> None:
            return None

    with pytest.raises(CommitLoaderError, match="GitHub API 호출 실패"):
        await load_from_github_pr(
            "o", "r", 1, github_token="t", client=_BoomClient()  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_load_from_github_pr_truncates_diff() -> None:
    pr_url = "https://api.github.com/repos/o/r/pulls/3"
    big = "z" * (MAX_DIFF_BYTES + 200)
    responses = {
        (pr_url, "json"): _FakeResponse(
            json_data={"base": {"ref": "main"}, "head": {"ref": "h"}}
        ),
        (pr_url + "/commits", "json"): _FakeResponse(json_data=[]),
        (pr_url, "diff"): _FakeResponse(text=big),
    }
    fake_client = _FakeAsyncClient(responses)
    payload = await load_from_github_pr(
        "o", "r", 3, github_token="t", client=fake_client  # type: ignore[arg-type]
    )
    assert payload.truncated is True


@pytest.mark.asyncio
async def test_load_from_github_pr_creates_own_client_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """client=None일 때 모듈이 자체 AsyncClient를 만들고 close하는지 확인."""
    pr_url = "https://api.github.com/repos/o/r/pulls/4"
    responses = {
        (pr_url, "json"): _FakeResponse(
            json_data={"base": {"ref": "main"}, "head": {"ref": "h"}}
        ),
        (pr_url + "/commits", "json"): _FakeResponse(json_data=[]),
        (pr_url, "diff"): _FakeResponse(text=""),
    }
    closed = {"v": False}
    fake_client = _FakeAsyncClient(responses)

    async def _aclose_track() -> None:
        closed["v"] = True

    fake_client.aclose = _aclose_track  # type: ignore[assignment]

    def _factory(*args: Any, **kwargs: Any) -> _FakeAsyncClient:
        return fake_client

    monkeypatch.setattr(commit_loader.httpx, "AsyncClient", _factory)
    payload = await load_from_github_pr("o", "r", 4, github_token="t")
    assert payload.base_branch == "main"
    assert closed["v"] is True
