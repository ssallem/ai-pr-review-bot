"""diff_loader 단위 테스트."""

from __future__ import annotations

import io
import sys
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pr_reviewer import diff_loader
from pr_reviewer.diff_loader import (
    DiffPayload,
    FileDiff,
    load_from_file,
    load_from_github_pr,
    load_from_stdin,
)


def test_load_from_file_parses_two_files(tmp_path, sample_diff_text):
    diff_path = tmp_path / "changes.diff"
    diff_path.write_text(sample_diff_text, encoding="utf-8")

    payload = load_from_file(str(diff_path))

    assert isinstance(payload, DiffPayload)
    assert len(payload.files) == 2
    filenames = {f.filename for f in payload.files}
    assert filenames == {"src/foo.py", "src/bar.js"}

    foo = next(f for f in payload.files if f.filename == "src/foo.py")
    assert foo.language == "python"
    assert foo.additions >= 3
    assert foo.deletions >= 1

    bar = next(f for f in payload.files if f.filename == "src/bar.js")
    assert bar.language == "javascript"
    assert bar.additions == 1


def test_load_from_file_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_from_file("nonexistent-file.diff")


def test_load_from_stdin_reads_stdin(monkeypatch, sample_diff_text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(sample_diff_text))
    payload = load_from_stdin()
    assert len(payload.files) == 2
    assert payload.total_additions > 0


def test_load_from_stdin_empty_raises(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("   \n"))
    with pytest.raises(ValueError):
        load_from_stdin()


def test_truncation_when_diff_exceeds_limit(monkeypatch):
    # 임계값을 작게 낮춰 truncation 경로 강제 발동.
    monkeypatch.setattr(diff_loader, "MAX_DIFF_BYTES", 200)
    monkeypatch.setattr(diff_loader, "MAX_FILE_HUNK_BYTES", 100)

    big_chunk = "+" + ("a" * 500)
    text = (
        "diff --git a/big.txt b/big.txt\n"
        "--- a/big.txt\n"
        "+++ b/big.txt\n"
        "@@ -1,1 +1,1 @@\n"
        f"{big_chunk}\n"
    )

    payload = diff_loader._build_payload(text)
    assert payload.truncated is True
    assert payload.notes  # 메모 기록됨
    assert len(payload.files) == 1
    assert "[truncated" in payload.files[0].hunks


def test_language_detection_unknown_ext():
    text = (
        "diff --git a/weird.xyz b/weird.xyz\n"
        "--- a/weird.xyz\n"
        "+++ b/weird.xyz\n"
        "@@ -1 +1 @@\n"
        "+changed\n"
    )
    payload = diff_loader._build_payload(text)
    assert payload.files[0].language == "text"


def test_no_diff_header_yields_empty_files():
    payload = diff_loader._build_payload("just some text without diff headers")
    assert payload.files == []


@pytest.mark.asyncio
async def test_load_from_github_pr_success(monkeypatch, sample_diff_text, github_token):
    """httpx.AsyncClient를 mock해 GitHub PR 두 endpoint를 시뮬레이션."""

    meta_response = MagicMock(spec=httpx.Response)
    meta_response.status_code = 200
    meta_response.json = MagicMock(
        return_value={
            "title": "Add feature",
            "body": "PR body text",
            "user": {"login": "octocat"},
            "base": {"ref": "main"},
            "head": {"ref": "feature/x"},
            "html_url": "https://github.com/o/r/pull/1",
        }
    )
    meta_response.raise_for_status = MagicMock()

    diff_response = MagicMock(spec=httpx.Response)
    diff_response.status_code = 200
    diff_response.text = sample_diff_text
    diff_response.raise_for_status = MagicMock()

    get_mock = AsyncMock(side_effect=[meta_response, diff_response])

    fake_client = MagicMock()
    fake_client.get = get_mock
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(httpx, "AsyncClient", MagicMock(return_value=fake_client))

    payload = await load_from_github_pr("o", "r", 1, github_token)

    assert payload.pr_meta is not None
    assert payload.pr_meta["title"] == "Add feature"
    assert payload.pr_meta["author"] == "octocat"
    assert len(payload.files) == 2


@pytest.mark.asyncio
async def test_load_from_github_pr_requires_token():
    with pytest.raises(ValueError):
        await load_from_github_pr("o", "r", 1, "")


def test_filediff_to_dict_round_trip():
    fd = FileDiff(
        filename="a.py",
        language="python",
        additions=3,
        deletions=1,
        hunks="@@ ...",
    )
    d = fd.to_dict()
    assert d["filename"] == "a.py"
    assert d["additions"] == 3


def test_diffpayload_aggregates_counts(sample_diff_text):
    payload = diff_loader._build_payload(sample_diff_text)
    assert payload.total_additions >= 4
    assert payload.total_deletions >= 1
