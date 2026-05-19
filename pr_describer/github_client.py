"""GitHub PR 업데이트 + 로컬 CHANGELOG.md prepend."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx

from pr_describer.logging_utils import get_logger

logger = get_logger(__name__)

_GITHUB_API = "https://api.github.com"
_CHANGELOG_HEADER = "# Changelog\n\n변경 이력을 사용자 관점에서 한 줄씩 기록합니다.\n\n"


class GitHubClientError(RuntimeError):
    """github_client 모듈 공용 예외."""


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pr-describer/0.1",
    }


async def update_pr(
    owner: str,
    repo: str,
    pr_number: int,
    title: str,
    body: str,
    token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """GitHub PR의 title/body를 PATCH로 업데이트.

    Returns:
        GitHub API 응답 JSON (PR 메타).

    Raises:
        GitHubClientError: 인증/권한/네트워크 오류.
    """
    own = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}"
    payload = {"title": title, "body": body}
    try:
        try:
            resp = await client.patch(url, json=payload, headers=_headers(token))
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GitHubClientError(
                f"PR 업데이트 실패 ({exc.response.status_code}): {owner}/{repo}#{pr_number} "
                f"— {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GitHubClientError(f"PR 업데이트 네트워크 오류: {exc}") from exc

        data: dict[str, Any] = resp.json()
        logger.info("PR updated: %s/%s#%d", owner, repo, pr_number)
        return data
    finally:
        if own:
            await client.aclose()


async def prepend_to_changelog(
    repo_root: Path,
    entry: str,
    *,
    today: date | None = None,
) -> Path:
    """로컬 CHANGELOG.md 위에 한 줄을 prepend.

    파일이 없으면 헤더와 함께 생성. ``entry``는 줄바꿈으로 끝나는 한 줄을 권장
    (formatter.format_changelog_entry 결과 그대로 가능).

    Returns:
        수정된 CHANGELOG.md 경로.
    """
    repo_root = Path(repo_root)
    if not repo_root.exists() or not repo_root.is_dir():
        raise GitHubClientError(f"repo_root가 존재하지 않거나 디렉토리가 아닙니다: {repo_root}")

    path = repo_root / "CHANGELOG.md"
    normalized = entry if entry.endswith("\n") else entry + "\n"

    if path.exists():
        existing = path.read_text(encoding="utf-8")
        # 기존 헤더 보존: `# Changelog\n\n...` 형태면 헤더 직후에 prepend.
        if existing.startswith("# "):
            head, _, rest = existing.partition("\n\n")
            new_content = f"{head}\n\n{normalized}{rest}"
            if not new_content.endswith("\n"):
                new_content += "\n"
        else:
            new_content = normalized + existing
    else:
        new_content = _CHANGELOG_HEADER + normalized

    path.write_text(new_content, encoding="utf-8")
    logger.info("CHANGELOG.md prepended: %s", path)
    # 사용하지 않는 today 인자는 호출부 일관성을 위해 시그니처에 유지.
    _ = today
    return path
