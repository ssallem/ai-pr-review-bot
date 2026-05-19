"""커밋·diff 입력 소스. git 로컬 저장소 또는 GitHub PR API에서 로드."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from pr_describer.logging_utils import get_logger

logger = get_logger(__name__)

# diff 100KB 초과 시 모델 컨텍스트 보호용 truncation.
MAX_DIFF_BYTES = 100 * 1024
TRUNCATION_NOTICE = "\n\n... (diff truncated: exceeded 100KB) ...\n"

# git 명령은 list 인자로만 호출 (shell=False). subject/body 구분자는 ASCII 31 (Unit Separator)
# 사용 — 커밋 메시지에 흔한 `|` 충돌 회피.
_GIT_FIELD_SEP = "\x1f"
_GIT_RECORD_SEP = "\x1e"
_GIT_LOG_FORMAT = f"%H{_GIT_FIELD_SEP}%s{_GIT_FIELD_SEP}%b{_GIT_FIELD_SEP}%an{_GIT_RECORD_SEP}"


@dataclass(frozen=True)
class Commit:
    """커밋 한 건. SHA + subject + body + author."""

    sha: str
    subject: str
    body: str
    author: str


@dataclass
class CommitsPayload:
    """커밋 목록 + 통합 diff. describer 입력 단위."""

    commits: list[Commit]
    diff: str
    base_branch: str
    head_branch: str
    truncated: bool = field(default=False)


class CommitLoaderError(RuntimeError):
    """commit_loader 모듈 공용 예외."""


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    """git 명령을 안전하게 실행 (shell=False, 인자 list).

    실패 시 CommitLoaderError로 래핑.
    """
    cmd = ["git", *args]
    logger.debug("running git: %s", cmd)
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise CommitLoaderError("git 실행 파일을 찾을 수 없습니다. PATH 확인 필요.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise CommitLoaderError(
            f"git {' '.join(args)} 실패 (exit={exc.returncode}): {stderr}"
        ) from exc
    return result.stdout


def _parse_git_log(raw: str) -> list[Commit]:
    """`%H\\x1f%s\\x1f%b\\x1f%an\\x1e` 포맷 출력을 파싱."""
    commits: list[Commit] = []
    # 마지막 record separator 이후 빈 문자열 제거.
    for record in raw.split(_GIT_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        fields = record.split(_GIT_FIELD_SEP)
        if len(fields) < 4:
            logger.warning("malformed git log record skipped: %r", record)
            continue
        sha, subject, body, author = fields[0], fields[1], fields[2], fields[3]
        commits.append(
            Commit(
                sha=sha.strip(),
                subject=subject.strip(),
                body=body.strip(),
                author=author.strip(),
            )
        )
    return commits


def _truncate_diff(diff: str) -> tuple[str, bool]:
    """100KB 초과 시 자르고 안내 문구 부착. 반환: (잘린/원본 diff, truncated 여부)."""
    encoded = diff.encode("utf-8")
    if len(encoded) <= MAX_DIFF_BYTES:
        return diff, False
    cut = encoded[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
    logger.warning(
        "diff size %d bytes exceeds %d — truncating", len(encoded), MAX_DIFF_BYTES
    )
    return cut + TRUNCATION_NOTICE, True


def load_from_git(
    base: str = "main",
    head: str = "HEAD",
    *,
    cwd: Path | None = None,
) -> CommitsPayload:
    """로컬 git 저장소에서 base..head 범위의 커밋·diff를 로드.

    Args:
        base: 비교 기준 브랜치 (기본 ``main``).
        head: 대상 ref (기본 ``HEAD``).
        cwd: git 명령 실행 디렉토리. None이면 현재 작업 디렉토리.

    Raises:
        CommitLoaderError: git 미설치, 저장소 아님, 또는 ref 부재 등.
    """
    # 사전 체크: 현재 위치가 git 저장소인지.
    inside = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=cwd).strip()
    if inside != "true":
        raise CommitLoaderError(
            "현재 디렉토리가 git 저장소가 아닙니다. cwd 옵션을 설정하거나 저장소 안에서 실행하세요."
        )

    log_raw = _run_git(
        ["log", f"--pretty=format:{_GIT_LOG_FORMAT}", f"{base}..{head}"],
        cwd=cwd,
    )
    commits = _parse_git_log(log_raw)

    diff_raw = _run_git(["diff", f"{base}..{head}"], cwd=cwd)
    diff, truncated = _truncate_diff(diff_raw)

    logger.info(
        "loaded %d commits, diff %d bytes (truncated=%s) from git %s..%s",
        len(commits),
        len(diff.encode("utf-8")),
        truncated,
        base,
        head,
    )
    return CommitsPayload(
        commits=commits,
        diff=diff,
        base_branch=base,
        head_branch=head,
        truncated=truncated,
    )


# ----- GitHub PR API 경로 ---------------------------------------------------

_GITHUB_API = "https://api.github.com"


def _gh_headers(token: str) -> dict[str, str]:
    """GitHub API 공통 헤더."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pr-describer/0.1",
    }


def _diff_headers(token: str) -> dict[str, str]:
    """diff 미디어 타입 — PR 통합 diff를 raw로 받기 위함."""
    h = _gh_headers(token)
    h["Accept"] = "application/vnd.github.v3.diff"
    return h


async def load_from_github_pr(
    owner: str,
    repo: str,
    pr_number: int,
    github_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> CommitsPayload:
    """GitHub PR에서 커밋·diff를 가져온다.

    Args:
        owner: 저장소 소유자.
        repo: 저장소 이름.
        pr_number: PR 번호.
        github_token: PAT (repo 권한 필요).
        client: 테스트용 주입 가능한 httpx.AsyncClient.

    Raises:
        CommitLoaderError: 인증 실패, PR 부재, 네트워크 오류.
    """
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        pr_url = f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}"
        commits_url = f"{pr_url}/commits"

        try:
            pr_resp = await client.get(pr_url, headers=_gh_headers(github_token))
            pr_resp.raise_for_status()
            pr_meta: dict[str, Any] = pr_resp.json()

            commits_resp = await client.get(
                commits_url, headers=_gh_headers(github_token)
            )
            commits_resp.raise_for_status()
            commits_json = commits_resp.json()

            diff_resp = await client.get(pr_url, headers=_diff_headers(github_token))
            diff_resp.raise_for_status()
            diff_text = diff_resp.text
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise CommitLoaderError(
                f"GitHub API 오류 ({status}): {owner}/{repo}#{pr_number} — {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise CommitLoaderError(f"GitHub API 호출 실패: {exc}") from exc

        commits = [
            Commit(
                sha=item.get("sha", ""),
                subject=(item.get("commit", {}).get("message", "") or "").split("\n", 1)[0],
                body="\n".join(
                    (item.get("commit", {}).get("message", "") or "").split("\n")[1:]
                ).strip(),
                author=(item.get("commit", {}).get("author", {}) or {}).get("name", "")
                or (item.get("author", {}) or {}).get("login", ""),
            )
            for item in commits_json
        ]

        diff, truncated = _truncate_diff(diff_text)

        base_branch = (pr_meta.get("base", {}) or {}).get("ref", "main")
        head_branch = (pr_meta.get("head", {}) or {}).get("ref", "HEAD")

        logger.info(
            "loaded %d commits, diff %d bytes (truncated=%s) from GitHub %s/%s#%d",
            len(commits),
            len(diff.encode("utf-8")),
            truncated,
            owner,
            repo,
            pr_number,
        )
        return CommitsPayload(
            commits=commits,
            diff=diff,
            base_branch=base_branch,
            head_branch=head_branch,
            truncated=truncated,
        )
    finally:
        if own_client:
            await client.aclose()
