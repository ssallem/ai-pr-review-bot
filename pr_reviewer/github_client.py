"""GitHub API 클라이언트. PR 댓글 작성 (Issues API)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class GitHubApiError(RuntimeError):
    """GitHub API 호출 실패. status code와 응답 본문을 보존."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


async def post_pr_comment(
    owner: str,
    repo: str,
    pr_number: int,
    body: str,
    token: str,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """PR(=Issue)에 일반 댓글 작성. 리뷰 댓글이 아닌 일반 댓글이라 라인 지정 없이 동작."""
    if not token:
        raise GitHubApiError("GITHUB_TOKEN이 비어있음")

    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"body": body}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise GitHubApiError(f"네트워크 오류: {exc}") from exc

    if response.status_code >= 400:
        raise GitHubApiError(
            f"GitHub API {response.status_code}: 댓글 작성 실패",
            status_code=response.status_code,
            body=response.text,
        )

    try:
        return response.json()
    except ValueError as exc:
        raise GitHubApiError(f"응답 JSON 파싱 실패: {exc}", body=response.text) from exc
