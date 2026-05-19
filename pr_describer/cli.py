"""click 기반 CLI. `pr-describe` entry point."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click

from pr_describer import __version__
from pr_describer.commit_loader import (
    CommitLoaderError,
    CommitsPayload,
    load_from_git,
    load_from_github_pr,
)
from pr_describer.describer import PRDescription, describe_pr
from pr_describer.formatter import (
    format_changelog_entry,
    format_full_markdown,
    format_terminal,
)
from pr_describer.github_client import (
    GitHubClientError,
    prepend_to_changelog,
    update_pr,
)
from pr_describer.logging_utils import get_logger

logger = get_logger(__name__)

# `OWNER/REPO#N` 또는 `OWNER/REPO/pull/N` 형태 허용.
_PR_REF_RE = re.compile(
    r"^(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+)(?:#|/pull/)(?P<number>\d+)$"
)


def _parse_pr_ref(ref: str) -> tuple[str, str, int]:
    """`owner/repo#123` → (owner, repo, 123). 실패 시 click 에러."""
    m = _PR_REF_RE.match(ref.strip())
    if not m:
        raise click.BadParameter(
            f"PR 참조 형식이 잘못됐습니다: {ref!r}. 'owner/repo#123' 또는 'owner/repo/pull/123' 사용."
        )
    return m.group("owner"), m.group("repo"), int(m.group("number"))


def _emit(
    desc: PRDescription,
    *,
    output: str,
    color: bool,
) -> str:
    """선택된 출력 포맷으로 직렬화."""
    if output == "md":
        # 머신/사람 모두 읽기 좋게 title + body.
        return f"# {desc.title}\n\n{format_full_markdown(desc)}"
    if output == "json":
        payload: dict[str, Any] = asdict(desc)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    # default terminal
    return format_terminal(desc, color=color)


async def _run_async(
    *,
    base: str,
    head: str,
    from_github: str | None,
    update: str | None,
    prepend_changelog: bool,
    no_conventional: bool,
    output: str,
    model: str | None,
    color: bool,
) -> int:
    """CLI 비동기 본체. 반환은 process exit code."""
    payload: CommitsPayload
    if from_github:
        # GitHub에서 커밋·diff 로드 — token은 settings에서.
        from pr_describer.config import settings as _settings  # 지연 import: 테스트 격리.

        if not _settings.github_token:
            click.echo(
                "ERROR: --from-github 사용 시 GITHUB_TOKEN 환경변수가 필요합니다.",
                err=True,
            )
            return 2
        owner, repo, number = _parse_pr_ref(from_github)
        try:
            payload = await load_from_github_pr(
                owner, repo, number, _settings.github_token
            )
        except CommitLoaderError as exc:
            click.echo(f"ERROR: {exc}", err=True)
            return 3
    else:
        try:
            payload = load_from_git(base=base, head=head)
        except CommitLoaderError as exc:
            click.echo(f"ERROR: {exc}", err=True)
            return 3

    if not payload.commits and not payload.diff.strip():
        click.echo(
            "WARNING: 커밋과 diff가 모두 비어 있습니다. base/head를 확인하세요.",
            err=True,
        )

    desc = await describe_pr(
        payload,
        model=model,
        conventional=not no_conventional,
    )

    click.echo(_emit(desc, output=output, color=color))

    if update:
        from pr_describer.config import settings as _settings

        if not _settings.github_token:
            click.echo(
                "ERROR: --update 사용 시 GITHUB_TOKEN 환경변수가 필요합니다.",
                err=True,
            )
            return 2
        owner, repo, number = _parse_pr_ref(update)
        body = format_full_markdown(desc)
        try:
            await update_pr(owner, repo, number, desc.title, body, _settings.github_token)
            click.echo(f"OK: PR {owner}/{repo}#{number} 업데이트 완료", err=True)
        except GitHubClientError as exc:
            click.echo(f"ERROR: {exc}", err=True)
            return 4

    if prepend_changelog:
        entry = format_changelog_entry(desc)
        try:
            path = await prepend_to_changelog(Path.cwd(), entry)
            click.echo(f"OK: CHANGELOG 갱신 완료 → {path}", err=True)
        except GitHubClientError as exc:
            click.echo(f"ERROR: {exc}", err=True)
            return 5

    return 0


@click.command(
    name="pr-describe",
    help="커밋과 diff에서 PR title·description·changelog를 생성합니다.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("--base", default="main", show_default=True, help="비교 기준 브랜치 (git 모드).")
@click.option("--head", default="HEAD", show_default=True, help="대상 ref (git 모드).")
@click.option(
    "--from-github",
    "from_github",
    default=None,
    metavar="OWNER/REPO#N",
    help="git 대신 GitHub PR API로 커밋·diff 로드.",
)
@click.option(
    "--update",
    default=None,
    metavar="OWNER/REPO#N",
    help="생성 결과로 GitHub PR title/body 업데이트.",
)
@click.option(
    "--prepend-changelog",
    is_flag=True,
    default=False,
    help="현재 디렉토리의 CHANGELOG.md 맨 위에 entry 추가.",
)
@click.option(
    "--no-conventional",
    is_flag=True,
    default=False,
    help="컨벤셔널 커밋 형식을 끄고 free-form title 사용.",
)
@click.option(
    "--output",
    type=click.Choice(["md", "terminal", "json"]),
    default="terminal",
    show_default=True,
    help="표준 출력 포맷.",
)
@click.option(
    "--model",
    default=None,
    metavar="NAME",
    help="모델 ID 오버라이드 (미설정 시 settings.claude_model).",
)
@click.option(
    "--no-color",
    is_flag=True,
    default=False,
    help="terminal 출력의 ANSI 컬러를 끔.",
)
@click.version_option(__version__, prog_name="pr-describe")
def main(
    base: str,
    head: str,
    from_github: str | None,
    update: str | None,
    prepend_changelog: bool,
    no_conventional: bool,
    output: str,
    model: str | None,
    no_color: bool,
) -> None:
    """`pr-describe` 진입점. asyncio.run으로 wrap."""
    code = asyncio.run(
        _run_async(
            base=base,
            head=head,
            from_github=from_github,
            update=update,
            prepend_changelog=prepend_changelog,
            no_conventional=no_conventional,
            output=output,
            model=model,
            color=not no_color,
        )
    )
    sys.exit(code)


if __name__ == "__main__":  # pragma: no cover
    main()
