"""Tests for server/app.py WebSocket behavior (listener mode)."""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from server.app import app


@pytest.fixture
def client():
    return TestClient(app)


def _setup_session(ws):
    ws.send_json({"type": "start"})
    for _ in range(10):
        msg = ws.receive_json()
        if msg["type"] == "session_ready":
            return msg
    raise AssertionError("Did not receive session_ready")


def test_session_ready_after_start(client):
    with client.websocket_connect("/ws") as ws:
        msg = _setup_session(ws)
        assert msg["type"] == "session_ready"
        assert "phrase_auto_dismiss_s" in msg
        assert "min_speech_confidence" in msg
        assert "hesitation_pause_ms" in msg


def _drain_until(ws, wanted_type, max_msgs=20):
    for _ in range(max_msgs):
        msg = ws.receive_json()
        if msg["type"] == wanted_type:
            return msg
    raise AssertionError(f"Did not receive {wanted_type}")


def test_transcript_chunk_triggers_listener_phrases(client):
    """A transcript chunk routes to call_context_listener; phrases are forwarded."""
    mock_phrases = ["I understand.", "Could you clarify?", "Thank you."]

    with patch("server.app.call_context_listener", new=AsyncMock(return_value=mock_phrases)):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            ws.send_json({"type": "transcript", "text": "um I was wondering"})

            _drain_until(ws, "thinking")
            msg = _drain_until(ws, "phrases")
            assert msg["phrases"] == mock_phrases


def test_silent_listener_emits_idle(client):
    with patch("server.app.call_context_listener", new=AsyncMock(return_value=None)):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            ws.send_json({"type": "transcript", "text": "the deadline is friday"})

            _drain_until(ws, "thinking")
            _drain_until(ws, "idle")


def test_end_session_returns_recap(client):
    with client.websocket_connect("/ws") as ws:
        _setup_session(ws)
        ws.send_json({"type": "phrase_selected", "phrase": "Could you clarify?"})
        ws.send_json({"type": "end_session"})

        msg = _drain_until(ws, "recap")
        assert "Could you clarify?" in msg["phrases_used"]
