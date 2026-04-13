"""Tests for SessionState in commcopilot/session.py."""

import time
import pytest
from commcopilot.session import SessionState
from commcopilot.config import TRANSCRIPT_WINDOW


def test_session_defaults():
    state = SessionState()
    assert state.scenario == "office_hours"
    assert state.transcript_buffer == []
    assert state.phrases_used == []
    assert state.hesitation_count == 0
    assert state.awaiting_phrases is False
    assert state.session_id != ""
    assert state.created_at <= time.monotonic()


def test_transcript_buffer_sliding_window():
    """Sliding window should keep only the last TRANSCRIPT_WINDOW segments."""
    state = SessionState()
    for i in range(TRANSCRIPT_WINDOW + 5):
        state.transcript_buffer.append(f"segment {i}")
        if len(state.transcript_buffer) > TRANSCRIPT_WINDOW:
            state.transcript_buffer = state.transcript_buffer[-TRANSCRIPT_WINDOW:]

    assert len(state.transcript_buffer) == TRANSCRIPT_WINDOW
    # Oldest retained segment is segment 5 (0-indexed)
    assert state.transcript_buffer[0] == f"segment {5}"


def test_phrases_used_append():
    state = SessionState()
    state.phrases_used.append("Could you clarify that?")
    state.phrases_used.append("I understand, thank you.")
    assert len(state.phrases_used) == 2
    assert "Could you clarify that?" in state.phrases_used


def test_awaiting_phrases_gate():
    """awaiting_phrases=True should signal that a pipeline call is in flight."""
    state = SessionState()
    assert state.awaiting_phrases is False
    state.awaiting_phrases = True
    assert state.awaiting_phrases is True
    state.awaiting_phrases = False
    assert state.awaiting_phrases is False


def test_unique_session_ids():
    s1 = SessionState()
    s2 = SessionState()
    assert s1.session_id != s2.session_id
