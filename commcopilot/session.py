"""In-memory session state for CommCopilot WebSocket sessions."""

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    transcript_buffer: list[str] = field(default_factory=list)  # sliding window
    phrases_used: list[str] = field(default_factory=list)
    hesitation_count: int = 0
    awaiting_phrases: bool = False  # True while Orchestrate call is in flight
    created_at: float = field(default_factory=time.monotonic)

    # Listener-mode state. `thread_id` is a server-generated UUID that we pass
    # as X-IBM-THREAD-ID on every silent chunk so ContextAgent accumulates the
    # running conversation across calls. `last_transcript_at` is the monotonic
    # clock time of the most recent STT chunk; the pause-heartbeat task uses it
    # to inject a "[pause]" marker when the student has been silent too long.
    thread_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    last_transcript_at: float = field(default_factory=time.monotonic)
