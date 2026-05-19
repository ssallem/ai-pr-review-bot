"""github_client 단위 테스트. httpx는 모두 mock."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pr_reviewer.github_client import GitHubApiError, post_pr_comment


def _build_response(*, status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text
    if json_body is not None:
        response.json = MagicMock(return_value=json_body)
    else:
        response.json = MagicMock(side_effect=ValueError("no json"))
    return response


def _patch_async_client(monkeypatch, response: MagicMock) -> AsyncMock:
    post_mock = AsyncMock(return_value=response)
    fake_client = MagicMock()
    fake_client.post = post_mock
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(httpx, "AsyncClient", MagicMock(return_value=fake_client))
    return post_mock


@pytest.mark.asyncio
async def test_post_pr_comment_success(monkeypatch, github_token):
    response = _build_response(
        status_code=201,
        json_body={"id": 123, "html_url": "https://github.com/o/r/pull/1#issuecomment-123"},
    )
    post_mock = _patch_async_client(monkeypatch, response)

    result = await post_pr_comment("o", "r", 1, "review body", github_token)

    assert result["id"] == 123
    post_mock.assert_awaited_once()
    args, kwargs = post_mock.call_args
    assert args[0] == "https://api.github.com/repos/o/r/issues/1/comments"
    assert kwargs["json"] == {"body": "review body"}
    assert kwargs["headers"]["Authorization"] == f"Bearer {github_token}"


@pytest.mark.asyncio
async def test_post_pr_comment_missing_token():
    with pytest.raises(GitHubApiError) as exc:
        await post_pr_comment("o", "r", 1, "x", "")
    assert "비어있음" in str(exc.value)


@pytest.mark.asyncio
async def test_post_pr_comment_http_error(monkeypatch, github_token):
    response = _build_response(status_code=403, text="forbidden")
    _patch_async_client(monkeypatch, response)

    with pytest.raises(GitHubApiError) as exc:
        await post_pr_comment("o", "r", 1, "x", github_token)
    assert exc.value.status_code == 403
    assert exc.value.body == "forbidden"


@pytest.mark.asyncio
async def test_post_pr_comment_network_error(monkeypatch, github_token):
    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=httpx.ConnectError("boom"))
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(httpx, "AsyncClient", MagicMock(return_value=fake_client))

    with pytest.raises(GitHubApiError) as exc:
        await post_pr_comment("o", "r", 1, "x", github_token)
    assert "네트워크 오류" in str(exc.value)


@pytest.mark.asyncio
async def test_post_pr_comment_json_decode_error(monkeypatch, github_token):
    response = _build_response(status_code=201, json_body=None, text="not-json")
    _patch_async_client(monkeypatch, response)

    with pytest.raises(GitHubApiError) as exc:
        await post_pr_comment("o", "r", 1, "x", github_token)
    assert "JSON 파싱 실패" in str(exc.value)
