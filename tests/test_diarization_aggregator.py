"""Tests for the streaming MPM aggregator."""

import pytest

from commcopilot.diarization import LabeledChunk, SlidingWindowAggregator


def _add_and_vote(agg: SlidingWindowAggregator, text: str, label: str) -> LabeledChunk:
    """Add a chunk, then record a single-label vote for the current window."""
    chunk = agg.add_chunk(text=text, ts="")
    window = agg.current_window()
    labels = [label] * len(window)
    agg.record_votes(labels)
    return chunk


def test_empty_aggregator_has_nothing_to_finalize():
    agg = SlidingWindowAggregator(window_size=3)
    assert agg.pop_finalized() == []
    assert agg.flush() == []


def test_current_window_respects_size():
    agg = SlidingWindowAggregator(window_size=3)
    for i in range(5):
        agg.add_chunk(text=f"s{i}", ts="")
    window = agg.current_window()
    assert len(window) == 3
    assert [c.text for c in window] == ["s2", "s3", "s4"]


def test_pop_finalized_triggers_when_buffer_exceeds_window():
    agg = SlidingWindowAggregator(window_size=2)
    _add_and_vote(agg, "s0", "student")  # window=[s0]
    _add_and_vote(agg, "s1", "student")  # window=[s0,s1]
    # s0 has 2 votes now; s1 has 1 vote. Buffer size == window size, nothing pops.
    assert agg.pop_finalized() == []

    _add_and_vote(agg, "s2", "other")    # window=[s1,s2]
    # s0 exits. finalize.
    finalized = agg.pop_finalized()
    assert len(finalized) == 1
    assert finalized[0].text == "s0"
    assert finalized[0].final_label == "student"


def test_majority_vote_mixed():
    agg = SlidingWindowAggregator(window_size=3)
    chunk = agg.add_chunk(text="mixed", ts="")
    # Manually append mixed votes
    chunk.tentative_votes.extend(["student", "student", "other"])
    # Push 3 more chunks to evict `mixed`
    for i in range(3):
        agg.add_chunk(text=f"filler{i}", ts="")
    finalized = agg.pop_finalized()
    assert len(finalized) == 1
    assert finalized[0].text == "mixed"
    assert finalized[0].final_label == "student"


def test_tie_breaks_to_student_by_default():
    agg = SlidingWindowAggregator(window_size=3)
    chunk = agg.add_chunk(text="tie", ts="")
    chunk.tentative_votes.extend(["student", "other"])
    for i in range(3):
        agg.add_chunk(text=f"x{i}", ts="")
    finalized = agg.pop_finalized()
    assert finalized[0].final_label == "student"


def test_tie_breaks_to_other_when_configured():
    agg = SlidingWindowAggregator(window_size=3, tie_break_label="other")
    chunk = agg.add_chunk(text="tie", ts="")
    chunk.tentative_votes.extend(["student", "other"])
    for i in range(3):
        agg.add_chunk(text=f"x{i}", ts="")
    finalized = agg.pop_finalized()
    assert finalized[0].final_label == "other"


def test_no_votes_falls_back_to_tie_break():
    """A chunk that somehow never received any vote falls back to the tie-break label."""
    agg = SlidingWindowAggregator(window_size=2, tie_break_label="student")
    orphan = agg.add_chunk(text="orphan", ts="")  # never voted on
    for i in range(2):
        agg.add_chunk(text=f"x{i}", ts="")
    finalized = agg.pop_finalized()
    assert finalized[0].text == "orphan"
    assert finalized[0].tentative_votes == []
    assert finalized[0].final_label == "student"


def test_record_votes_ignores_unknown_labels():
    agg = SlidingWindowAggregator(window_size=2)
    c0 = agg.add_chunk(text="s0", ts="")
    c1 = agg.add_chunk(text="s1", ts="")
    agg.record_votes(["bogus", "student"])
    assert c0.tentative_votes == []
    assert c1.tentative_votes == ["student"]


def test_record_votes_truncates_excess():
    agg = SlidingWindowAggregator(window_size=2)
    c0 = agg.add_chunk(text="s0", ts="")
    c1 = agg.add_chunk(text="s1", ts="")
    agg.record_votes(["student", "other", "student"])
    assert c0.tentative_votes == ["student"]
    assert c1.tentative_votes == ["other"]


def test_flush_finalizes_remaining_chunks():
    agg = SlidingWindowAggregator(window_size=5)
    c0 = agg.add_chunk(text="s0", ts="")
    c1 = agg.add_chunk(text="s1", ts="")
    c2 = agg.add_chunk(text="s2", ts="")
    # Inject distinct votes per chunk so the majority differs clearly.
    c0.tentative_votes.extend(["student", "student"])
    c1.tentative_votes.extend(["other", "other"])
    c2.tentative_votes.extend(["student"])
    remaining = agg.flush()
    assert len(remaining) == 3
    assert all(c.final_label is not None for c in remaining)
    assert remaining[0].final_label == "student"
    assert remaining[1].final_label == "other"
    assert remaining[2].final_label == "student"


def test_flush_clears_buffer():
    agg = SlidingWindowAggregator(window_size=3)
    agg.add_chunk(text="s0", ts="")
    agg.flush()
    assert agg.current_window() == []
    assert agg.pop_finalized() == []


def test_mpm_scenario_nine_chunks_eight_window():
    """First chunk accumulates 8 votes across overlapping windows, then exits."""
    agg = SlidingWindowAggregator(window_size=8)
    first = agg.add_chunk(text="s0", ts="")
    # Simulate 8 DiarizationAgent rounds where first chunk stays in the window
    # and receives a vote each round. We emulate by manually appending.
    for i in range(1, 8):
        agg.add_chunk(text=f"s{i}", ts="")
    # 8 chunks in buffer, all in window. Each has 0 votes so far.
    agg.record_votes(["student"] * 8)
    # first now has 1 vote. Window still [s0..s7].
    assert len(first.tentative_votes) == 1

    # 9th chunk arrives; first exits after this round
    agg.add_chunk(text="s8", ts="")
    agg.record_votes(["student"] * 8)  # window is [s1..s8]; first not in it, gets no new vote
    finalized = agg.pop_finalized()
    assert len(finalized) == 1
    assert finalized[0].text == "s0"
    assert finalized[0].final_label == "student"


def test_invalid_window_size_raises():
    with pytest.raises(ValueError):
        SlidingWindowAggregator(window_size=0)


def test_invalid_tie_break_raises():
    with pytest.raises(ValueError):
        SlidingWindowAggregator(window_size=3, tie_break_label="both")


def test_index_monotonically_increases():
    agg = SlidingWindowAggregator(window_size=3)
    c0 = agg.add_chunk(text="a", ts="")
    c1 = agg.add_chunk(text="b", ts="")
    c2 = agg.add_chunk(text="c", ts="")
    assert (c0.index, c1.index, c2.index) == (0, 1, 2)
