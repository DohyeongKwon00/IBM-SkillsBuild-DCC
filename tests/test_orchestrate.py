"""Tests for commcopilot/orchestrate.py (listener mode)."""

import json
import pytest
from unittest.mock import AsyncMock, patch

from commcopilot.orchestrate import (
    call_context_listener,
    _strip_fences,
    _is_silent_response,
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
            thread_id="tid-1",
            phrases_used=[],
        )
    assert result is None


@pytest.mark.asyncio
async def test_listener_returns_phrases_on_hesitation():
    phrases = ["Could you clarify?", "I see, thank you."]
    with _mock_iam, patch("commcopilot.orchestrate._chat", new=AsyncMock(return_value=json.dumps(phrases))):
        result = await call_context_listener(
            chunk="um I was wondering",
            thread_id="tid-1",
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
            thread_id="tid-1",
            phrases_used=[],
        )
    assert result == phrases


@pytest.mark.asyncio
async def test_listener_parse_failure_returns_none():
    with _mock_iam, patch("commcopilot.orchestrate._chat", new=AsyncMock(return_value="not json but long enough")):
        result = await call_context_listener(
            chunk="um",
            thread_id="tid-1",
            phrases_used=[],
        )
    assert result is None


@pytest.mark.asyncio
async def test_listener_passes_thread_id():
    captured = {}

    async def mock_chat(agent_id, prompt, thread_id=None, warmup=False):
        captured["prompt"] = prompt
        captured["thread_id"] = thread_id
        return ""

    with _mock_iam, patch("commcopilot.orchestrate._chat", side_effect=mock_chat):
        await call_context_listener(
            chunk="hello professor",
            thread_id="tid-42",
            phrases_used=[],
        )
    assert "hello professor" in captured["prompt"]
    assert captured["thread_id"] == "tid-42"


@pytest.mark.asyncio
async def test_listener_prompt_includes_dual_mic_context():
    captured = {}

    async def mock_chat(agent_id, prompt, thread_id=None, warmup=False):
        captured["prompt"] = prompt
        return ""

    with _mock_iam, patch("commcopilot.orchestrate._chat", side_effect=mock_chat):
        await call_context_listener(
            chunk="[Speaker A]: um I wanted to ask about my grade",
            thread_id="tid-42",
            phrases_used=[],
            conversation_history=[
                "[Speaker B]: What can I help you with?",
                "[Speaker A]: um I wanted to ask about my grade",
            ],
            current_user="Speaker A",
            ai_solution_user="Speaker A",
            known_speakers=["Speaker A", "Speaker B"],
        )

    assert "current_user: Speaker A" in captured["prompt"]
    assert "ai_solution_user: Speaker A" in captured["prompt"]
    assert 'known_speakers: ["Speaker A", "Speaker B"]' in captured["prompt"]
    assert "[Speaker B]: What can I help you with?" in captured["prompt"]
