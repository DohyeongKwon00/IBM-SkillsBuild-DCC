"""Tests for SessionState in commcopilot/session.py."""

import time

from commcopilot.config import MPM_TIE_BREAK_LABEL, MPM_WINDOW_SIZE, TRANSCRIPT_WINDOW
from commcopilot.diarization import SlidingWindowAggregator
from commcopilot.session import SessionState


def test_session_defaults():
    state = SessionState()
    assert state.transcript_buffer == []
    assert state.phrases_used == []
    assert state.hesitation_count == 0
    assert state.awaiting_phrases is False
    assert state.session_id != ""
    assert state.created_at <= time.monotonic()


def test_threads_are_split_and_unique():
    state = SessionState()
    assert state.context_thread_id != ""
    assert state.diarization_thread_id != ""
    assert state.context_thread_id != state.diarization_thread_id


def test_aggregator_configured_from_settings():
    state = SessionState()
    assert isinstance(state.aggregator, SlidingWindowAggregator)
    assert state.aggregator.window_size == MPM_WINDOW_SIZE
    assert state.aggregator.tie_break_label == MPM_TIE_BREAK_LABEL


def test_each_session_gets_its_own_aggregator():
    s1 = SessionState()
    s2 = SessionState()
    assert s1.aggregator is not s2.aggregator


def test_transcript_buffer_sliding_window():
    """Sliding window should keep only the last TRANSCRIPT_WINDOW segments."""
    state = SessionState()
    for i in range(TRANSCRIPT_WINDOW + 5):
        state.transcript_buffer.append(f"segment {i}")
        if len(state.transcript_buffer) > TRANSCRIPT_WINDOW:
            state.transcript_buffer = state.transcript_buffer[-TRANSCRIPT_WINDOW:]

    assert len(state.transcript_buffer) == TRANSCRIPT_WINDOW
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
