"""Tests for server/app.py WebSocket behavior (AssemblyAI STT mode).

AssemblyAI STT integration is mocked via AssemblyAISTTClient — tests verify the
WebSocket control flow without requiring real API credentials.
"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from server.app import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stt_mock():
    """Return an AssemblyAISTTClient mock that connects successfully."""
    mock = AsyncMock()
    mock.connect = AsyncMock()
    mock.send_audio = AsyncMock()
    mock.close = AsyncMock()
    return mock


def _setup_session(ws):
    """Send start handshake and wait for session_ready."""
    ws.send_json({"type": "start"})
    for _ in range(10):
        msg = ws.receive_json()
        if msg["type"] == "session_ready":
            return msg
        if msg["type"] == "error":
            raise AssertionError(f"Got error during setup: {msg}")
    raise AssertionError("Did not receive session_ready")


def _drain_until(ws, wanted_type, max_msgs=20):
    for _ in range(max_msgs):
        msg = ws.receive_json()
        if msg["type"] == wanted_type:
            return msg
    raise AssertionError(f"Did not receive {wanted_type!r} within {max_msgs} messages")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def stt_mock():
    return _make_stt_mock()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_session_ready_after_start(client, stt_mock):
    with (
        patch("server.app.AssemblyAISTTClient", return_value=stt_mock),
        patch("server.app.ASSEMBLYAI_API_KEY", "fake-key"),
    ):
        with client.websocket_connect("/ws") as ws:
            msg = _setup_session(ws)
            assert msg["type"] == "session_ready"
            assert "phrase_auto_dismiss_s" in msg


def test_error_when_credentials_missing(client):
    """Server sends error message when AssemblyAI API key is not configured."""
    with patch("server.app.ASSEMBLYAI_API_KEY", ""):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "start"})
            for _ in range(5):
                msg = ws.receive_json()
                if msg["type"] == "error":
                    assert "not configured" in msg["message"].lower()
                    return
    pytest.fail("Expected error message for missing credentials")


def test_phrase_selected_stored(client, stt_mock):
    with (
        patch("server.app.AssemblyAISTTClient", return_value=stt_mock),
        patch("server.app.ASSEMBLYAI_API_KEY", "fake-key"),
    ):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            ws.send_json({"type": "phrase_selected", "phrase": "Could you clarify?"})
            ws.send_json({"type": "end_session"})

            msg = _drain_until(ws, "recap")
            assert "Could you clarify?" in msg["phrases_used"]


def test_end_session_returns_recap(client, stt_mock):
    with (
        patch("server.app.AssemblyAISTTClient", return_value=stt_mock),
        patch("server.app.ASSEMBLYAI_API_KEY", "fake-key"),
    ):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            ws.send_json({"type": "end_session"})

            msg = _drain_until(ws, "recap")
            assert msg["type"] == "recap"
            assert "recap" in msg
