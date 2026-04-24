"""AssemblyAI real-time Speech to Text WebSocket client (per-session).

Audio flow:
    Browser (AudioContext, PCM16, 16 kHz, 4096-sample chunks ~256ms)
    -> binary WebSocket frames -> FastAPI
    -> AssemblyAISTTClient.send_audio()
    -> AssemblyAI Streaming WebSocket (wss://streaming.assemblyai.com/v3/ws)
    -> Turn messages with speaker_label + end_of_turn flag
    -> on_transcript callback("[Speaker A]: text") on end_of_turn

Message types from AssemblyAI:
    Begin       -> session opened, log session ID
    Turn        -> transcript chunk; emit only when end_of_turn=True
    Termination -> session stats, normal close
"""

import asyncio
import json
import logging
from urllib.parse import urlencode
from typing import Awaitable, Callable

import websockets
import websockets.exceptions

from commcopilot.config import ASSEMBLYAI_API_KEY

logger = logging.getLogger(__name__)

OnTranscript = Callable[[str], Awaitable[None]]

_AAI_WS_BASE = "wss://streaming.assemblyai.com/v3/ws"
_SAMPLE_RATE = 16000
_SPEECH_MODEL = "u3-rt-pro"


class AssemblyAISTTClient:
    """Manages a single AssemblyAI real-time WebSocket connection per session."""

    def __init__(self, on_transcript: OnTranscript) -> None:
        self._ws = None
        self._on_transcript = on_transcript
        self._receive_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Open AssemblyAI streaming WebSocket."""
        if not ASSEMBLYAI_API_KEY:
            raise RuntimeError("ASSEMBLYAI_API_KEY must be set")

        params = urlencode({
            "sample_rate": _SAMPLE_RATE,
            "speech_model": _SPEECH_MODEL,
            "speaker_labels": "true",
            "max_speakers": 2,
            "language_code": "en",
        })
        url = f"{_AAI_WS_BASE}?{params}"

        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": ASSEMBLYAI_API_KEY},
        )

        logger.info("AssemblyAI STT connected")
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def send_audio(self, data: bytes) -> None:
        """Forward a PCM16 audio frame from the browser to AssemblyAI."""
        if self._ws:
            try:
                await self._ws.send(data)
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Tried to send audio after AssemblyAI closed")

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")

                if msg_type == "Begin":
                    logger.info("AssemblyAI session ID: %s", msg.get("id"))

                elif msg_type == "Turn":
                    transcript = msg.get("transcript", "").strip()
                    end_of_turn = msg.get("end_of_turn", False)
                    speaker = msg.get("speaker_label") or "A"

                    if not transcript:
                        continue

                    if end_of_turn:
                        # Final utterance — emit to UI and ContextAgent.
                        chunk = f"[Speaker {speaker}]: {transcript}"
                        logger.info("Final turn: %r", chunk)
                        asyncio.create_task(self._on_transcript(chunk))
                    # Partial turns are ignored server-side; UI gets them separately.

                elif msg_type == "Termination":
                    logger.info(
                        "AssemblyAI session terminated: %.1fs audio",
                        msg.get("audio_duration_seconds", 0),
                    )

        except websockets.exceptions.ConnectionClosed:
            logger.info("AssemblyAI STT connection closed")
        except Exception as e:
            logger.error("AssemblyAI receive loop error: %s", e)

    async def close(self) -> None:
        """Terminate the AssemblyAI session and close the WebSocket."""
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        if self._ws:
            try:
                await self._ws.send(json.dumps({"type": "Terminate"}))
                await self._ws.close()
            except Exception:
                pass
