"""Tests for commcopilot/orchestrate.py (listener mode)."""

import json
import pytest
from unittest.mock import AsyncMock, patch

from commcopilot.diarization import LabeledChunk
from commcopilot.orchestrate import (
    _is_silent_response,
    _parse_diarization_labels,
    _strip_fences,
    call_context_listener,
    call_diarization_agent,
)

_mock_iam = patch("commcopilot.orchestrate._get_iam_token", new=AsyncMock(return_value="test-token"))


def test_strip_fences_json_block():
    raw = '```json\n["phrase 1", "phrase 2"]\n```'
    assert _strip_fences(raw) == '["phrase 1", "phrase 2"]'


def test_strip_fences_plain_text_unchanged():
    raw = '["phrase 1", "phrase 2"]'
    assert _strip_fences(raw) == raw


def test_is_silent_empty():
    assert _is_silent_response("") is True
    assert _is_silent_response("   ") is True
    assert _is_silent_response("``` ```") is True


def test_is_silent_short_filler():
    assert _is_silent_response("ok") is True
    assert _is_silent_response(".") is True


def test_is_silent_phrases_not_silent():
    assert _is_silent_response('["Could you clarify?"]') is False


@pytest.mark.asyncio
async def test_listener_returns_none_on_silent():
    with _mock_iam, patch("commcopilot.orchestrate._chat", new=AsyncMock(return_value="")):
        result = await call_context_listener(
            chunk="so the deadline is tomorrow",
            recent_context=[],
            phrases_used=[],
        )
    assert result is None


@pytest.mark.asyncio
async def test_listener_returns_phrases_on_hesitation():
    phrases = ["Could you clarify?", "I see, thank you."]
    with _mock_iam, patch("commcopilot.orchestrate._chat", new=AsyncMock(return_value=json.dumps(phrases))):
        result = await call_context_listener(
            chunk="um I was wondering",
            recent_context=[],
            phrases_used=[],
        )
    assert result == phrases


@pytest.mark.asyncio
async def test_listener_strips_fences():
    phrases = ["A", "B", "C"]
    fenced = f"```json\n{json.dumps(phrases)}\n```"
    with _mock_iam, patch("commcopilot.orchestrate._chat", new=AsyncMock(return_value=fenced)):
        result = await call_context_listener(
            chunk="uh",
            recent_context=[],
            phrases_used=[],
        )
    assert result == phrases


@pytest.mark.asyncio
async def test_listener_parse_failure_returns_none():
    with _mock_iam, patch("commcopilot.orchestrate._chat", new=AsyncMock(return_value="not json but long enough")):
        result = await call_context_listener(
            chunk="um",
            recent_context=[],
            phrases_used=[],
        )
    assert result is None


@pytest.mark.asyncio
async def test_listener_includes_context_in_prompt():
    captured = {}

    async def mock_chat(agent_id, prompt, thread_id=None, warmup=False):
        captured["prompt"] = prompt
        captured["thread_id"] = thread_id
        return ""

    ctx = ["[other] [ts] Hello.", "[student] [ts] Hi there."]
    with _mock_iam, patch("commcopilot.orchestrate._chat", side_effect=mock_chat):
        await call_context_listener(
            chunk="[student] [ts] hello professor",
            recent_context=ctx,
            phrases_used=[],
        )
    assert "hello professor" in captured["prompt"]
    assert "[other] [ts] Hello." in captured["prompt"]
    assert captured["thread_id"] is None


# --- Diarization ---

def _window(*texts: str) -> list[LabeledChunk]:
    return [LabeledChunk(text=t, ts="", index=i) for i, t in enumerate(texts)]


def test_parse_diarization_labels_basic():
    assert _parse_diarization_labels('["student", "other"]', expected_len=2) == ["student", "other"]


def test_parse_diarization_labels_fenced():
    raw = '```json\n["student", "student", "other"]\n```'
    assert _parse_diarization_labels(raw, expected_len=3) == ["student", "student", "other"]


def test_parse_diarization_labels_case_insensitive():
    assert _parse_diarization_labels('["STUDENT", "Other"]', expected_len=2) == ["student", "other"]


def test_parse_diarization_labels_drops_unknown():
    assert _parse_diarization_labels('["student", "unknown", "other"]', expected_len=3) == ["student", "other"]


def test_parse_diarization_labels_truncates_excess():
    assert _parse_diarization_labels('["student", "other", "student"]', expected_len=2) == ["student", "other"]


def test_parse_diarization_labels_invalid_json_returns_none():
    assert _parse_diarization_labels("not json at all", expected_len=2) is None


def test_parse_diarization_labels_non_array_returns_none():
    assert _parse_diarization_labels('{"label": "student"}', expected_len=1) is None


def test_parse_diarization_labels_empty_returns_none():
    assert _parse_diarization_labels("", expected_len=1) is None
    assert _parse_diarization_labels("[]", expected_len=1) is None


@pytest.mark.asyncio
async def test_diarization_returns_labels():
    with _mock_iam, patch(
        "commcopilot.orchestrate._chat",
        new=AsyncMock(return_value='["student", "other"]'),
    ):
        labels = await call_diarization_agent(
            window=_window("Hi.", "Hello."),
            thread_id="did-1",
        )
    assert labels == ["student", "other"]


@pytest.mark.asyncio
async def test_diarization_returns_none_on_empty_window():
    labels = await call_diarization_agent(window=[], thread_id="did-1")
    assert labels is None


@pytest.mark.asyncio
async def test_diarization_returns_none_on_parse_failure():
    with _mock_iam, patch(
        "commcopilot.orchestrate._chat",
        new=AsyncMock(return_value="not json"),
    ):
        labels = await call_diarization_agent(
            window=_window("Hi."),
            thread_id="did-1",
        )
    assert labels is None


@pytest.mark.asyncio
async def test_diarization_passes_thread_id_and_formats_window():
    captured = {}

    async def mock_chat(agent_id, prompt, thread_id=None, warmup=False):
        captured["prompt"] = prompt
        captured["thread_id"] = thread_id
        return '["student", "other"]'

    with _mock_iam, patch("commcopilot.orchestrate._chat", side_effect=mock_chat):
        await call_diarization_agent(
            window=_window("Hi there.", "Hello, how can I help?"),
            thread_id="did-42",
        )
    assert captured["thread_id"] == "did-42"
    assert "Hi there." in captured["prompt"]
    assert "Hello, how can I help?" in captured["prompt"]
    assert "[0]" in captured["prompt"] and "[1]" in captured["prompt"]
