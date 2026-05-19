"""github_client 단위 테스트 — httpx mock + tmp_path 활용."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from pr_describer.github_client import (
    GitHubClientError,
    prepend_to_changelog,
    update_pr,
)


# ---------- httpx mock 헬퍼 ----------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("PATCH", "https://api.github.com"),
                response=httpx.Response(self.status_code, text=self.text),
            )


class _FakePatchClient:
    """httpx.AsyncClient를 흉내냄. patch 메서드만 지원."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.closed = False

    async def patch(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append((url, json or {}, headers or {}))
        return self._response

    async def aclose(self) -> None:
        self.closed = True


# ---------- update_pr --------------------------------------------------------


async def test_update_pr_happy_path_sends_patch_and_returns_json() -> None:
    """200 OK → PATCH 호출되고 JSON이 그대로 반환되어야 한다."""
    expected_json = {"number": 7, "title": "new title", "body": "new body"}
    client = _FakePatchClient(_FakeResponse(status_code=200, json_data=expected_json))

    result = await update_pr(
        owner="o",
        repo="r",
        pr_number=7,
        title="new title",
        body="new body",
        token="ghp_xyz",
        client=client,  # type: ignore[arg-type]
    )

    assert result == expected_json
    assert len(client.calls) == 1
    url, payload, headers = client.calls[0]
    assert url == "https://api.github.com/repos/o/r/pulls/7"
    assert payload == {"title": "new title", "body": "new body"}
    assert headers["Authorization"] == "Bearer ghp_xyz"
    # 주입 client는 외부 책임 — aclose 호출되지 않아야 한다.
    assert client.closed is False


async def test_update_pr_raises_on_4xx() -> None:
    """HTTP 4xx → GitHubClientError로 래핑되어야 한다."""
    client = _FakePatchClient(_FakeResponse(status_code=404, text="Not Found"))

    with pytest.raises(GitHubClientError, match="404"):
        await update_pr(
            owner="o",
            repo="r",
            pr_number=99,
            title="t",
            body="b",
            token="ghp_xyz",
            client=client,  # type: ignore[arg-type]
        )


async def test_update_pr_wraps_network_error() -> None:
    """네트워크 레벨 오류 → GitHubClientError로 변환."""

    class _BoomClient:
        async def patch(self, *args: Any, **kwargs: Any) -> Any:
            raise httpx.ConnectError("connection refused")

        async def aclose(self) -> None:
            return None

    with pytest.raises(GitHubClientError, match="네트워크"):
        await update_pr(
            owner="o",
            repo="r",
            pr_number=1,
            title="t",
            body="b",
            token="ghp_xyz",
            client=_BoomClient(),  # type: ignore[arg-type]
        )


# ---------- prepend_to_changelog ---------------------------------------------


async def test_prepend_to_changelog_creates_file_when_missing(tmp_path: Path) -> None:
    """기존 CHANGELOG.md가 없을 때 — 헤더 + 신규 entry로 생성."""
    entry = "- 2026-05-10 (feat): 새 기능 추가\n"
    path = await prepend_to_changelog(tmp_path, entry)

    assert path == tmp_path / "CHANGELOG.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    # 기본 헤더가 포함되어야 함.
    assert content.startswith("# Changelog")
    # entry는 헤더 다음에 위치.
    assert entry in content


async def test_prepend_to_changelog_preserves_existing_content(tmp_path: Path) -> None:
    """기존 파일이 있고 헤더가 있으면 — 헤더 직후에 prepend, 기존 entry 보존."""
    initial = (
        "# Changelog\n\n변경 이력입니다.\n\n"
        "- 2026-05-01 (fix): 기존 entry 보존\n"
    )
    path = tmp_path / "CHANGELOG.md"
    path.write_text(initial, encoding="utf-8")

    new_entry = "- 2026-05-10 (feat): 새 entry\n"
    await prepend_to_changelog(tmp_path, new_entry)

    final = path.read_text(encoding="utf-8")
    # 헤더는 유지.
    assert final.startswith("# Changelog")
    # 기존 entry 보존.
    assert "기존 entry 보존" in final
    # 새 entry 추가.
    assert "새 entry" in final
    # 새 entry가 기존 entry보다 위에 있어야 함 (prepend).
    assert final.index("새 entry") < final.index("기존 entry 보존")


async def test_prepend_to_changelog_appends_newline_if_missing(tmp_path: Path) -> None:
    """entry가 줄바꿈으로 끝나지 않아도 정상화되어야 한다."""
    entry_no_newline = "- 2026-05-10 (chore): 줄바꿈 없는 entry"
    await prepend_to_changelog(tmp_path, entry_no_newline)

    content = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "줄바꿈 없는 entry\n" in content


async def test_prepend_to_changelog_raises_when_repo_root_missing(tmp_path: Path) -> None:
    """repo_root 자체가 없거나 디렉토리가 아니면 — GitHubClientError."""
    missing = tmp_path / "nope"  # 존재하지 않음.
    with pytest.raises(GitHubClientError, match="존재하지 않거나"):
        await prepend_to_changelog(missing, "- entry\n")
