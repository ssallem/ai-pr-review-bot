"""reviewer 단위 테스트. anthropic SDK는 모두 mock."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pr_reviewer import diff_loader, reviewer
from pr_reviewer.diff_loader import DiffPayload, FileDiff
from pr_reviewer.reviewer import (
    ReviewResult,
    _build_user_message,
    _parse_response,
    _strip_code_fence,
    review_diff,
)


def _build_text_response(text: str) -> MagicMock:
    """anthropic 응답 객체 모양 흉내."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _make_diff(sample_diff_text: str) -> DiffPayload:
    return diff_loader._build_payload(sample_diff_text)


def test_strip_code_fence_with_json_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _strip_code_fence(raw) == '{"a": 1}'


def test_strip_code_fence_no_fence_passthrough():
    raw = '{"a": 1}'
    assert _strip_code_fence(raw) == '{"a": 1}'


def test_parse_response_well_formed(sample_review_json):
    issues, summary, warnings_list = _parse_response(sample_review_json)
    assert len(issues) == 2
    assert issues[0].severity == "critical"
    assert issues[0].file == "src/foo.py"
    assert issues[0].suggested_fix is not None
    assert "한 건의 critical" in summary
    assert warnings_list == []


def test_parse_response_invalid_json_falls_back():
    issues, summary, warnings_list = _parse_response("not json at all")
    assert issues == []
    assert summary == "not json at all"
    assert any("JSON 파싱 실패" in w for w in warnings_list)


def test_parse_response_missing_fields_uses_defaults():
    raw = '{"issues": [{}], "summary": ""}'
    issues, summary, warnings_list = _parse_response(raw)
    assert len(issues) == 1
    assert issues[0].severity == "suggestion"
    assert issues[0].category == "style"
    assert summary == ""


def test_parse_response_non_object_falls_back():
    raw = "[1, 2, 3]"
    issues, summary, warnings_list = _parse_response(raw)
    assert issues == []
    assert summary == raw
    assert warnings_list


def test_parse_response_non_dict_issue_item_skipped():
    raw = '{"issues": ["bad", {"severity": "warning", "file": "x", "category": "style", "message": "ok"}], "summary": "s"}'
    issues, _, warnings_list = _parse_response(raw)
    assert len(issues) == 1
    assert any("객체가 아님" in w for w in warnings_list)


def test_build_user_message_includes_pr_meta_and_files(sample_diff_text):
    payload = _make_diff(sample_diff_text)
    payload.pr_meta = {
        "title": "Hello",
        "author": "octocat",
        "base_ref": "main",
        "head_ref": "feat/x",
        "html_url": "https://x.example/pr/1",
        "body": "long body",
    }
    msg = _build_user_message(payload)
    assert "Hello" in msg
    assert "octocat" in msg
    assert "src/foo.py" in msg
    assert "diff (unified format)" in msg


def test_build_user_message_includes_truncation_note():
    payload = DiffPayload(
        files=[FileDiff("a.py", "python", 1, 0, "+x")],
        truncated=True,
        notes=["a.py: 절단됨"],
    )
    msg = _build_user_message(payload)
    assert "절단됨" in msg


@pytest.mark.asyncio
async def test_review_diff_happy_path(sample_diff_text, sample_review_json):
    diff = _make_diff(sample_diff_text)

    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_build_text_response(sample_review_json))

    result = await review_diff(diff, model="claude-test", client=fake_client)

    assert isinstance(result, ReviewResult)
    assert len(result.issues) == 2
    assert result.issues[0].severity == "critical"
    assert "critical" in result.summary or "발견" in result.summary
    assert result.warnings == []

    # prompt cache가 시스템 프롬프트에 적용됐는지 확인.
    args, kwargs = fake_client.messages.create.call_args
    assert kwargs["model"] == "claude-test"
    assert kwargs["max_tokens"] == 4000
    system = kwargs["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"]["type"] == "ephemeral"


@pytest.mark.asyncio
async def test_review_diff_uses_settings_model_when_unset(sample_diff_text, sample_review_json):
    diff = _make_diff(sample_diff_text)
    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_build_text_response(sample_review_json))

    await review_diff(diff, client=fake_client)

    _args, kwargs = fake_client.messages.create.call_args
    assert kwargs["model"]  # 비지 않음
    assert kwargs["model"] == reviewer.settings.claude_model


@pytest.mark.asyncio
async def test_review_diff_empty_files_short_circuits():
    empty = DiffPayload(files=[])
    result = await review_diff(empty, client=MagicMock())
    assert result.issues == []
    assert "변경된 파일이 없" in result.summary


@pytest.mark.asyncio
async def test_review_diff_invalid_json_warning(sample_diff_text):
    diff = _make_diff(sample_diff_text)
    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=_build_text_response("이건 JSON이 아닙니다.")
    )

    result = await review_diff(diff, client=fake_client)
    assert result.issues == []
    assert any("JSON 파싱 실패" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_review_diff_truncated_propagates_warning(sample_diff_text, sample_review_json):
    diff = _make_diff(sample_diff_text)
    diff.truncated = True
    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_build_text_response(sample_review_json))

    result = await review_diff(diff, client=fake_client)
    assert any("절단" in w for w in result.warnings)


def test_extract_text_from_dict_response():
    raw = {"content": [{"type": "text", "text": "hello"}]}
    assert reviewer._extract_text(raw) == "hello"


def test_extract_text_no_content_returns_empty():
    response = MagicMock()
    response.content = None
    assert reviewer._extract_text(response) == ""
