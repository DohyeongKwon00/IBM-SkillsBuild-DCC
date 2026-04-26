"""In-memory session state for CommCopilot WebSocket sessions."""

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    transcript_buffer: list[str] = field(default_factory=list)
    phrases_used: list[str] = field(default_factory=list)
    recent_transcripts: list[dict] = field(default_factory=list)
    hesitation_count: int = 0
    awaiting_phrases: bool = False
    created_at: float = field(default_factory=time.monotonic)
    thread_id: str = field(default_factory=lambda: str(uuid.uuid4()))
