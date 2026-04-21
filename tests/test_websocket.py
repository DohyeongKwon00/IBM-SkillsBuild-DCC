"""Tests for server/app.py WebSocket behavior (diarization + listener)."""

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


def _drain_until(ws, wanted_type, max_msgs=50):
    for _ in range(max_msgs):
        msg = ws.receive_json()
        if msg["type"] == wanted_type:
            return msg
    raise AssertionError(f"Did not receive {wanted_type}")


def test_session_ready_after_start(client):
    with client.websocket_connect("/ws") as ws:
        msg = _setup_session(ws)
        assert msg["type"] == "session_ready"
        assert "phrase_auto_dismiss_s" in msg
        assert "min_speech_confidence" in msg


def test_student_chunk_flushed_on_end_triggers_phrases(client):
    """A student-labeled chunk flushed via end_session reaches ContextAgent and returns phrases."""
    mock_phrases = ["I understand.", "Could you clarify?", "Thank you."]
    listener_inputs: list[str] = []

    async def mock_listener(chunk, recent_context, phrases_used, on_event=None):
        listener_inputs.append(chunk)
        return mock_phrases

    with patch("server.app.call_diarization_agent", new=AsyncMock(return_value=["student"])), \
         patch("server.app.call_context_listener", side_effect=mock_listener):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            ws.send_json({"type": "transcript", "text": "um I was wondering"})
            ws.send_json({"type": "end_session"})

            _drain_until(ws, "thinking")
            phrases_msg = _drain_until(ws, "phrases")
            assert phrases_msg["phrases"] == mock_phrases
            _drain_until(ws, "recap")

    assert len(listener_inputs) == 1
    assert listener_inputs[0].startswith("[student]")
    assert "um I was wondering" in listener_inputs[0]


def test_other_chunk_routes_to_context_with_other_prefix(client):
    """An 'other'-labeled chunk still reaches ContextAgent, but with [other] prefix."""
    listener_inputs: list[str] = []

    async def mock_listener(chunk, recent_context, phrases_used, on_event=None):
        listener_inputs.append(chunk)
        return None  # context agent returns empty for [other]

    with patch("server.app.call_diarization_agent", new=AsyncMock(return_value=["other"])), \
         patch("server.app.call_context_listener", side_effect=mock_listener):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            ws.send_json({"type": "transcript", "text": "The deadline is Friday."})
            ws.send_json({"type": "end_session"})

            _drain_until(ws, "thinking")
            _drain_until(ws, "idle")
            _drain_until(ws, "recap")

    assert len(listener_inputs) == 1
    assert listener_inputs[0].startswith("[other]")


def test_diarization_failure_falls_back_to_tie_break(client):
    """When DiarizationAgent fails, flushed chunks still finalize via tie-break = student."""
    listener_inputs: list[str] = []

    async def mock_listener(chunk, recent_context, phrases_used, on_event=None):
        listener_inputs.append(chunk)
        return None

    with patch("server.app.call_diarization_agent", new=AsyncMock(return_value=None)), \
         patch("server.app.call_context_listener", side_effect=mock_listener):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            ws.send_json({"type": "transcript", "text": "something"})
            ws.send_json({"type": "end_session"})

            _drain_until(ws, "recap")

    assert len(listener_inputs) == 1
    assert listener_inputs[0].startswith("[student]")  # tie-break default


def test_end_session_returns_recap(client):
    with patch("server.app.call_diarization_agent", new=AsyncMock(return_value=None)):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            ws.send_json({"type": "phrase_selected", "phrase": "Could you clarify?"})
            ws.send_json({"type": "end_session"})

            msg = _drain_until(ws, "recap")
            assert "Could you clarify?" in msg["phrases_used"]


def test_empty_transcript_ignored(client):
    """Empty text is skipped before reaching the aggregator."""
    diarize_mock = AsyncMock(return_value=None)
    with patch("server.app.call_diarization_agent", new=diarize_mock):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            ws.send_json({"type": "transcript", "text": "   "})
            ws.send_json({"type": "end_session"})
            _drain_until(ws, "recap")
    diarize_mock.assert_not_called()


def test_mpm_finalization_via_window_rollover(client):
    """With enough chunks, earliest chunks finalize via window rollover (not flush)."""
    listener_inputs: list[str] = []

    async def mock_listener(chunk, recent_context, phrases_used, on_event=None):
        listener_inputs.append(chunk)
        return None

    # Always return "student" labels for whatever window size is passed
    async def mock_diarize(window, thread_id, on_event=None):
        return ["student"] * len(window)

    with patch("server.app.call_diarization_agent", side_effect=mock_diarize), \
         patch("server.app.call_context_listener", side_effect=mock_listener):
        with client.websocket_connect("/ws") as ws:
            _setup_session(ws)
            # Send MPM_WINDOW_SIZE + 2 chunks so at least 2 chunks exit the window
            from commcopilot.config import MPM_WINDOW_SIZE
            for i in range(MPM_WINDOW_SIZE + 2):
                ws.send_json({"type": "transcript", "text": f"sentence {i}"})
            ws.send_json({"type": "end_session"})

            _drain_until(ws, "recap")

    # Every chunk should have been routed to the listener, all [student]
    assert len(listener_inputs) == MPM_WINDOW_SIZE + 2
    assert all(s.startswith("[student]") for s in listener_inputs)
    # Order preserved: sentence 0 first, etc.
    assert "sentence 0" in listener_inputs[0]
    assert f"sentence {MPM_WINDOW_SIZE + 1}" in listener_inputs[-1]
