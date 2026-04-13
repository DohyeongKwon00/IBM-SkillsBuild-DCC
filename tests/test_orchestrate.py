"""Tests for commcopilot/orchestrate.py."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from commcopilot.orchestrate import (
    call_supervisor_agent,
    call_pipeline_sequential,
    get_phrases,
    _strip_fences,
    OrchestrateTimeoutError,
    OrchestrateParseError,
)
from commcopilot.config import FALLBACK_PHRASES

# Patch IAM token fetch for all tests that call _chat
_mock_iam = patch("commcopilot.orchestrate._get_iam_token", new=AsyncMock(return_value="test-token"))


# --- _strip_fences ---

def test_strip_fences_json_block():
    raw = '```json\n["phrase 1", "phrase 2"]\n```'
    result = _strip_fences(raw)
    assert result == '["phrase 1", "phrase 2"]'


def test_strip_fences_plain_text_unchanged():
    raw = '["phrase 1", "phrase 2"]'
    assert _strip_fences(raw) == raw


# --- call_supervisor_agent ---

@pytest.mark.asyncio
async def test_supervisor_happy_path():
    phrases = ["Could you clarify?", "I see, thank you.", "Could you repeat that?"]
    with _mock_iam, patch("commcopilot.orchestrate._chat", new=AsyncMock(return_value=json.dumps(phrases))):
        result = await call_supervisor_agent("office_hours", ["um the deadline"], [])
    assert result == phrases


@pytest.mark.asyncio
async def test_supervisor_strips_fences():
    phrases = ["Phrase A", "Phrase B", "Phrase C"]
    fenced = f"```json\n{json.dumps(phrases)}\n```"
    with _mock_iam, patch("commcopilot.orchestrate._chat", new=AsyncMock(return_value=fenced)):
        result = await call_supervisor_agent("office_hours", ["transcript"], [])
    assert result == phrases


@pytest.mark.asyncio
async def test_supervisor_parse_error_raises():
    with _mock_iam, patch("commcopilot.orchestrate._chat", new=AsyncMock(return_value="not json at all")):
        with pytest.raises(OrchestrateParseError):
            await call_supervisor_agent("office_hours", ["transcript"], [])


@pytest.mark.asyncio
async def test_supervisor_warmup_returns_empty():
    with _mock_iam, patch("commcopilot.orchestrate._chat", new=AsyncMock(return_value='["p1", "p2"]')):
        result = await call_supervisor_agent("office_hours", ["hello"], [], warmup=True)
    assert result == []


# --- call_pipeline_sequential ---

@pytest.mark.asyncio
async def test_sequential_happy_path():
    ctx_response = '{"role": "professor", "tone": "formal", "intent": "ask about grade"}'
    phrase_response = '["Could you explain?", "I see.", "Thank you."]'
    safety_response = '["Could you explain?", "I see.", "Thank you."]'

    responses = [ctx_response, phrase_response, safety_response]
    call_count = 0

    async def mock_chat(agent_id, prompt, warmup=False):
        nonlocal call_count
        r = responses[call_count]
        call_count += 1
        return r

    with _mock_iam, patch("commcopilot.orchestrate._chat", side_effect=mock_chat):
        result = await call_pipeline_sequential("office_hours", ["transcript"], [])

    assert len(result) == 3
    assert "Could you explain?" in result


@pytest.mark.asyncio
async def test_sequential_context_failure_uses_defaults():
    """If ContextAgent returns garbage, pipeline continues with default context."""
    phrase_response = '["phrase 1", "phrase 2", "phrase 3"]'
    safety_response = '["phrase 1", "phrase 2", "phrase 3"]'

    responses = ["INVALID_JSON", phrase_response, safety_response]
    call_count = 0

    async def mock_chat(agent_id, prompt, warmup=False):
        nonlocal call_count
        r = responses[call_count]
        call_count += 1
        return r

    with _mock_iam, patch("commcopilot.orchestrate._chat", side_effect=mock_chat):
        result = await call_pipeline_sequential("office_hours", ["transcript"], [])

    assert len(result) > 0


# --- get_phrases (entry point) ---

@pytest.mark.asyncio
async def test_get_phrases_returns_fallback_on_error():
    with patch("commcopilot.orchestrate.call_supervisor_agent", new=AsyncMock(side_effect=Exception("boom"))):
        result = await get_phrases("office_hours", ["transcript"], [], use_supervisor=True)
    assert result == list(FALLBACK_PHRASES)


@pytest.mark.asyncio
async def test_get_phrases_uses_sequential_when_flag_false():
    expected = ["p1", "p2", "p3"]
    with patch("commcopilot.orchestrate.call_pipeline_sequential", new=AsyncMock(return_value=expected)):
        result = await get_phrases("office_hours", ["transcript"], [], use_supervisor=False)
    assert result == expected
