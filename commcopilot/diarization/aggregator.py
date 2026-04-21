"""Streaming MPM aggregator for text-based speaker diarization."""

from commcopilot.diarization.types import LabeledChunk


class SlidingWindowAggregator:
    """Maintains a sliding window of recent chunks and aggregates MPM votes.

    Pipeline per new chunk:
        1. `add_chunk(text, ts)` appends to the buffer as tentative.
        2. Caller asks for `current_window()`, sends it to DiarizationAgent,
           receives one label per window chunk.
        3. `record_votes(labels)` appends each label to the matching chunk's
           tentative_votes.
        4. `pop_finalized()` removes chunks that have exited the window and
           assigns each a final_label via majority vote over tentative_votes.

    At session end, `flush()` finalizes anything still inside the window.

    Ties break to `tie_break_label` (default "student") — the demo prefers a
    false positive (unnecessary phrase suggestion) over a false negative
    (missed hesitation).
    """

    def __init__(self, window_size: int = 8, tie_break_label: str = "student") -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        if tie_break_label not in ("student", "other"):
            raise ValueError("tie_break_label must be 'student' or 'other'")
        self.window_size = window_size
        self.tie_break_label = tie_break_label
        self._buffer: list[LabeledChunk] = []
        self._next_index = 0

    def add_chunk(self, text: str, ts: str) -> LabeledChunk:
        chunk = LabeledChunk(text=text, ts=ts, index=self._next_index)
        self._next_index += 1
        self._buffer.append(chunk)
        return chunk

    def current_window(self) -> list[LabeledChunk]:
        return self._buffer[-self.window_size:]

    def record_votes(self, labels: list[str]) -> None:
        window = self.current_window()
        for chunk, label in zip(window, labels):
            if label in ("student", "other"):
                chunk.tentative_votes.append(label)

    def pop_finalized(self) -> list[LabeledChunk]:
        finalized: list[LabeledChunk] = []
        while len(self._buffer) > self.window_size:
            chunk = self._buffer.pop(0)
            chunk.final_label = self._majority(chunk.tentative_votes)
            finalized.append(chunk)
        return finalized

    def flush(self) -> list[LabeledChunk]:
        remaining = self._buffer[:]
        self._buffer.clear()
        for chunk in remaining:
            chunk.final_label = self._majority(chunk.tentative_votes)
        return remaining

    def _majority(self, votes: list[str]) -> str:
        if not votes:
            return self.tie_break_label
        student = votes.count("student")
        other = votes.count("other")
        if student > other:
            return "student"
        if other > student:
            return "other"
        return self.tie_break_label
