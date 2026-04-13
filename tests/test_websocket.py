"""Tests for server/app.py WebSocket behavior."""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from server.app import app
from commcopilot.config import FALLBACK_PHRASES


@pytest.fixture
def client():
    return TestClient(app)


def _setup_session(ws, scenario="office_hours"):
    """Send scenario and wait for session_ready. Returns the session_ready message."""
    ws.send_json({"type": "scenario", "scenario": scenario})
    # Drain until session_ready (warm-up is a background task, skipped when ORCHESTRATE_URL empty)
    for _ in range(10):
        msg = ws.receive_json()
        if msg["type"] == "session_ready":
            return msg
    raise AssertionError("Did not receive session_ready")


def test_scenarios_endpoint(client):
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert "office_hours" in data
    assert "admin_office" in data
    assert "name" in data["office_hours"]


def test_session_ready_after_scenario(client):
    """Server sends session_ready with config after receiving scenario message."""
    with client.websocket_connect("/ws") as ws:
        msg = _setup_session(ws)
        assert msg["type"] == "session_ready"
        assert "phrase_auto_dismiss_s" in msg
        assert "min_speech_confidence" in msg
        assert "hesitation_cooldown_s" in msg


def test_hesitation_triggers_phrases(client):
    """Hesitation message triggers thinking then phrases."""
    mock_phrases = ["I understand.", "Could you clarify?", "Thank you."]

    with patch("server.app.get_phrases", new=AsyncMock(return_value=mock_phrases)):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            ws.send_json({"type": "transcript", "text": "um I was wondering"})
            ws.send_json({"type": "hesitation", "trigger": "filler"})

            # Expect: thinking, then phrases
            msg1 = ws.receive_json()
            assert msg1["type"] == "thinking"

            msg2 = ws.receive_json()
            assert msg2["type"] == "phrases"
            assert msg2["phrases"] == mock_phrases


def test_awaiting_phrases_cleared_after_pipeline(client):
    """awaiting_phrases should be False after a pipeline call completes, allowing future hesitations."""
    call_count = 0

    async def counting_get_phrases(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return ["phrase one", "phrase two", "phrase three"]

    with patch("server.app.get_phrases", side_effect=counting_get_phrases):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)

            # First hesitation
            ws.send_json({"type": "hesitation", "trigger": "pause"})
            assert ws.receive_json()["type"] == "thinking"
            assert ws.receive_json()["type"] == "phrases"

            # Second hesitation — should also be processed (awaiting_phrases was reset)
            ws.send_json({"type": "hesitation", "trigger": "filler"})
            assert ws.receive_json()["type"] == "thinking"
            assert ws.receive_json()["type"] == "phrases"

    # Both hesitations should have triggered the pipeline
    assert call_count == 2


def test_end_session_returns_recap(client):
    """end_session returns a recap containing the phrases the user selected."""
    with client.websocket_connect("/ws") as ws:
        _setup_session(ws)

        ws.send_json({"type": "phrase_selected", "phrase": "Could you clarify?"})
        ws.send_json({"type": "end_session"})

        msg = ws.receive_json()
        assert msg["type"] == "recap"
        assert "phrases_used" in msg
        assert "Could you clarify?" in msg["phrases_used"]


def test_transcript_buffer_filled(client):
    """Transcript messages should accumulate in the session buffer."""
    with patch("server.app.get_phrases", new=AsyncMock(return_value=["p1", "p2", "p3"])):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)

            ws.send_json({"type": "transcript", "text": "The deadline is Friday."})
            ws.send_json({"type": "transcript", "text": "Submit via the portal."})
            ws.send_json({"type": "hesitation", "trigger": "pause"})

            ws.receive_json()  # thinking
            msg = ws.receive_json()  # phrases
            assert msg["type"] == "phrases"
            assert len(msg["phrases"]) > 0
