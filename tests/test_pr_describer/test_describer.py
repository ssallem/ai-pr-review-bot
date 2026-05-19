"""describer 단위 테스트 — anthropic AsyncAnthropic mock 사용.

`describe_pr`는 client를 주입 가능하므로 fake async client로 외부 호출을 차단한다.
asyncio_mode = "auto" 이므로 async def 테스트는 자동 실행된다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pr_describer.commit_loader import Commit, CommitsPayload
from pr_describer.describer import PRDescription, describe_pr

# ---------- 테스트 헬퍼 ----------------------------------------------------------


class _FakeContentBlock:
    """anthropic SDK의 content block을 흉내내는 객체. `.text` 속성만 있으면 충분."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    """messages.create의 반환값. `.content`는 block 리스트."""

    def __init__(self, text: str) -> None:
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
    """messages.create를 mock하는 async 메서드 컨테이너."""

    def __init__(self, response_text: str, *, raise_exc: Exception | None = None) -> None:
        self._text = response_text
        self._raise = raise_exc
        self.last_kwargs: dict[str, Any] | None = None
        self.call_count = 0

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.call_count += 1
        self.last_kwargs = kwargs
        if self._raise is not None:
            raise self._raise
        return _FakeResponse(self._text)


class _FakeAsyncAnthropic:
    """주입용 가짜 클라이언트. messages 속성만 충실히 구현."""

    def __init__(self, response_text: str = "", *, raise_exc: Exception | None = None) -> None:
        self.messages = _FakeMessages(response_text, raise_exc=raise_exc)
        self.closed = False

    async def close(self) -> None:  # 사용되진 않음(client 주입 시 close skip), 안전망.
        self.closed = True


def _payload(diff: str = "diff --git a/x b/x\n+y\n") -> CommitsPayload:
    return CommitsPayload(
        commits=[Commit(sha="abc1234567", subject="feat: add x", body="본문", author="Alice")],
        diff=diff,
        base_branch="main",
        head_branch="feature/x",
        truncated=False,
    )


# ---------- 테스트 -------------------------------------------------------------


async def test_describe_pr_happy_path_parses_json() -> None:
    """정상 JSON 응답 → PRDescription 필드가 그대로 매핑되어야 한다."""
    body = {
        "title": "feat(http): add gzip compression",
        "description": "## 변경 요약\n압축 추가\n",
        "changelog_entry": "HTTP 응답에 gzip 압축이 적용됩니다",
        "breaking_change": False,
        "type": "feat",
    }
    client = _FakeAsyncAnthropic(json.dumps(body))
    desc = await describe_pr(_payload(), client=client)  # type: ignore[arg-type]

    assert isinstance(desc, PRDescription)
    assert desc.title == body["title"]
    assert desc.description == body["description"].strip()
    assert desc.changelog_entry == body["changelog_entry"]
    assert desc.breaking_change is False
    assert desc.type == "feat"
    assert client.messages.call_count == 1


async def test_describe_pr_falls_back_on_invalid_json() -> None:
    """JSON으로 파싱 불가능한 raw text → fallback PRDescription 반환."""
    raw = "이건 JSON이 아니라 자연어 응답입니다."
    client = _FakeAsyncAnthropic(raw)
    desc = await describe_pr(_payload(), client=client, conventional=True)  # type: ignore[arg-type]

    # fallback 분기: title은 conventional이면 "chore: " prefix 포함.
    assert desc.title.startswith("chore: ")
    assert desc.description == raw
    assert desc.changelog_entry.startswith("내부:")
    assert desc.type == "chore"
    assert desc.breaking_change is False
    # raw 원문은 보존되어야 한다.
    assert desc.raw_response == raw


async def test_describe_pr_conventional_toggle_reflected_in_prompt() -> None:
    """conventional=True/False 두 호출에서 system 프롬프트가 달라야 한다."""
    body = {
        "title": "x",
        "description": "y",
        "changelog_entry": "z",
        "breaking_change": False,
        "type": "chore",
    }

    client_true = _FakeAsyncAnthropic(json.dumps(body))
    await describe_pr(_payload(), client=client_true, conventional=True)  # type: ignore[arg-type]
    sys_true = client_true.messages.last_kwargs["system"]  # type: ignore[index]
    sys_text_true = sys_true[0]["text"]

    client_false = _FakeAsyncAnthropic(json.dumps(body))
    await describe_pr(_payload(), client=client_false, conventional=False)  # type: ignore[arg-type]
    sys_false = client_false.messages.last_kwargs["system"]  # type: ignore[index]
    sys_text_false = sys_false[0]["text"]

    # 모드 안내 문구가 토글마다 다르게 포함되어야 함.
    assert "컨벤셔널 커밋 형식" in sys_text_true
    assert "free-form" in sys_text_false
    assert sys_text_true != sys_text_false
    # cache_control이 prompt cache용으로 설정되어 있는지 확인.
    assert sys_true[0]["cache_control"] == {"type": "ephemeral"}


async def test_describe_pr_propagates_api_failure() -> None:
    """anthropic API 호출이 실패하면 예외가 그대로 전파되어야 한다."""

    class _BoomError(RuntimeError):
        pass

    client = _FakeAsyncAnthropic("", raise_exc=_BoomError("upstream timeout"))
    with pytest.raises(_BoomError, match="upstream timeout"):
        await describe_pr(_payload(), client=client)  # type: ignore[arg-type]


async def test_describe_pr_handles_code_fence_wrapped_json() -> None:
    """모델이 ```json ... ``` 코드펜스로 감싸도 파싱 성공해야 한다."""
    body = {
        "title": "refactor: extract module",
        "description": "## 변경 요약\n모듈 분리",
        "changelog_entry": "내부: 모듈 분리",
        "breaking_change": True,
        "type": "refactor",
    }
    wrapped = "```json\n" + json.dumps(body) + "\n```"
    client = _FakeAsyncAnthropic(wrapped)
    desc = await describe_pr(_payload(), client=client)  # type: ignore[arg-type]

    assert desc.title == body["title"]
    assert desc.breaking_change is True
    assert desc.type == "refactor"


async def test_describe_pr_normalizes_unknown_type() -> None:
    """모델이 invalid type을 반환하면 'chore'로 정규화되어야 한다."""
    body = {
        "title": "x",
        "description": "y",
        "changelog_entry": "z",
        "breaking_change": "yes",  # 문자열로 와도 boolean으로 coerce.
        "type": "bogus_type",
    }
    client = _FakeAsyncAnthropic(json.dumps(body))
    desc = await describe_pr(_payload(), client=client)  # type: ignore[arg-type]

    assert desc.type == "chore"
    assert desc.breaking_change is True
