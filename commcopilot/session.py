"""In-memory session state for CommCopilot WebSocket sessions."""

import time
import uuid
from dataclasses import dataclass, field

from commcopilot.config import MPM_TIE_BREAK_LABEL, MPM_WINDOW_SIZE
from commcopilot.diarization import SlidingWindowAggregator


def _new_aggregator() -> SlidingWindowAggregator:
    return SlidingWindowAggregator(
        window_size=MPM_WINDOW_SIZE,
        tie_break_label=MPM_TIE_BREAK_LABEL,
    )


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    transcript_buffer: list[str] = field(default_factory=list)
    phrases_used: list[str] = field(default_factory=list)
    hesitation_count: int = 0
    awaiting_phrases: bool = False
    created_at: float = field(default_factory=time.monotonic)
    context_thread_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    diarization_thread_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    aggregator: SlidingWindowAggregator = field(default_factory=_new_aggregator)
