"""Data types for streaming diarization."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LabeledChunk:
    """A transcript sentence moving through the MPM pipeline.

    `tentative_votes` accumulates labels from every DiarizationAgent call that
    included this chunk in its window. `final_label` is set once the chunk
    exits the active window and its votes are aggregated via majority voting.
    """

    text: str
    ts: str
    index: int
    tentative_votes: list[str] = field(default_factory=list)
    final_label: Optional[str] = None
