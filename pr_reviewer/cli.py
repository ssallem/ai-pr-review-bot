"""`pr-review` CLI 진입점. click + asyncio."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from typing import Any

import click

logger = logging.getLogger(__name__)

# OWNER/REPO#NUMBER 형식.
_PR_SPEC_RE = re.compile(r"^([\w.-]+)/([\w.-]+)#(\d+)$")


def _parse_pr_spec(value: str) -> tuple[str, str, int]:
    match = _PR_SPEC_RE.match(value.strip())
    if not match:
        raise click.BadParameter(
            "--pr 형식 오류. 예: octocat/hello-world#42",
            param_hint="--pr",
        )
    return match.group(1), match.group(2), int(match.group(3))


@click.command(name="pr-review")
@click.option(
    "--pr",
    "pr_spec",
    default=None,
    help="GitHub PR 식별자 (OWNER/REPO#NUMBER 형식). 예: octocat/hello-world#42",
)
@click.option(
    "--from-file",
    "from_file",
    type=click.Path(exists=False, dir_okay=False),
    default=None,
    help="로컬 unified diff 파일 경로",
)
@click.option(
    "--from-stdin",
    "from_stdin",
    is_flag=True,
    default=False,
    help="stdin에서 diff 읽기 (예: `git diff main..HEAD | pr-review --from-stdin`)",
)
@click.option(
    "--post",
    is_flag=True,
    default=False,
    help="결과를 PR에 댓글로 작성 (--pr 와 함께 사용)",
)
@click.option(
    "--output",
    type=click.Choice(["md", "terminal", "json"], case_sensitive=False),
    default="terminal",
    help="출력 포맷",
)
@click.option(
    "--model",
    default=None,
    help="Claude 모델 ID 오버라이드 (미지정 시 CLAUDE_MODEL 또는 기본값)",
)
def main(
    pr_spec: str | None,
    from_file: str | None,
    from_stdin: bool,
    post: bool,
    output: str,
    model: str | None,
) -> None:
    """git diff 또는 GitHub PR에 한국어 AI 코드 리뷰를 작성한다."""
    sources = [bool(pr_spec), bool(from_file), from_stdin]
    if sum(sources) != 1:
        raise click.UsageError(
            "정확히 하나의 입력 소스가 필요: --pr / --from-file / --from-stdin"
        )

    if post and not pr_spec:
        raise click.UsageError("--post 는 --pr 과 함께만 사용 가능")

    try:
        exit_code = asyncio.run(
            _run(
                pr_spec=pr_spec,
                from_file=from_file,
                from_stdin=from_stdin,
                post=post,
                output=output.lower(),
                model=model,
            )
        )
    except click.ClickException:
        raise
    except Exception as exc:
        click.echo(f"오류: {exc}", err=True)
        sys.exit(2)

    sys.exit(exit_code)


async def _run(
    *,
    pr_spec: str | None,
    from_file: str | None,
    from_stdin: bool,
    post: bool,
    output: str,
    model: str | None,
) -> int:
    """실제 비동기 워크플로우. CLI에서 분리해 테스트하기 쉽게."""
    # lazy import: 임포트 시 ANTHROPIC_API_KEY 검증을 trigger 하므로 --help는 통과시킨다.
    from .config import settings
    from .diff_loader import (
        DiffPayload,
        load_from_file,
        load_from_github_pr,
        load_from_stdin,
    )
    from .formatter import format_as_json, format_as_markdown, format_as_terminal
    from .github_client import GitHubApiError, post_pr_comment
    from .reviewer import ReviewResult, review_diff

    diff: DiffPayload
    if pr_spec:
        owner, repo, number = _parse_pr_spec(pr_spec)
        if not settings.github_token:
            raise click.UsageError("--pr 사용 시 GITHUB_TOKEN 환경 변수 필수")
        diff = await load_from_github_pr(owner, repo, number, settings.github_token)
    elif from_file:
        diff = load_from_file(from_file)
    else:
        diff = load_from_stdin()

    if diff.truncated:
        click.echo(
            f"[경고] diff 크기 {diff.raw_size_bytes} bytes, 일부 절단됨",
            err=True,
        )

    result: ReviewResult = await review_diff(diff, model=model)

    if output == "md":
        rendered = format_as_markdown(result)
    elif output == "json":
        rendered = format_as_json(result)
    else:
        rendered = format_as_terminal(result)

    click.echo(rendered)

    if post and pr_spec:
        owner, repo, number = _parse_pr_spec(pr_spec)
        comment_body = format_as_markdown(result)
        try:
            posted: dict[str, Any] = await post_pr_comment(
                owner, repo, number, comment_body, settings.github_token or ""
            )
        except GitHubApiError as exc:
            click.echo(f"[오류] PR 댓글 작성 실패: {exc}", err=True)
            return 3
        click.echo(f"[성공] 댓글 작성됨: {posted.get('html_url', '(URL 없음)')}", err=True)

    # critical 이슈 있으면 비제로 종료 코드 (CI에서 머지 차단 트리거 가능).
    has_critical = any(i.severity == "critical" for i in result.issues)
    return 1 if has_critical else 0


if __name__ == "__main__":
    main()
