"""AssemblyAI real-time Speech to Text WebSocket client.

Audio flow:
    Browser (AudioContext, PCM16, 16 kHz, 4096-sample chunks ~256ms)
    -> binary WebSocket frames -> FastAPI
    -> AssemblyAISTTClient.send_audio()
    -> AssemblyAI Streaming WebSocket (wss://streaming.assemblyai.com/v3/ws)
    -> Turn messages with turn_is_formatted=True flag
    -> on_transcript callback("text") on formatted turn

Connection params:
    format_turns=True       enables punctuated/capitalized final transcripts
    language_detection=False fixed to English
    end_of_turn_confidence_threshold / min_end_of_turn_silence / max_turn_silence / vad_threshold
                            tuned for natural turn detection
"""

import asyncio
import json
import logging
import ssl
from urllib.parse import urlencode
from typing import Awaitable, Callable

import certifi
import websockets
import websockets.exceptions

from commcopilot.config import ASSEMBLYAI_API_KEY

logger = logging.getLogger(__name__)

OnTranscript = Callable[[str], Awaitable[None]]

_AAI_WS_BASE = "wss://streaming.assemblyai.com/v3/ws"
_SAMPLE_RATE = 16000
_SPEECH_MODEL = "u3-rt-pro"

_CONNECTION_PARAMS = {
    "sample_rate": _SAMPLE_RATE,
    "speech_model": _SPEECH_MODEL,
    "format_turns": "true",
    "end_of_turn_confidence_threshold": 0.4,
    "min_end_of_turn_silence_when_confident": 100,
    "max_turn_silence": 1000,
    "vad_threshold": 0.4,
    "language_detection": "false",
}


class AssemblyAISTTClient:
    """Manages one AssemblyAI real-time WebSocket connection for one audio source."""

    def __init__(self, on_transcript: OnTranscript, source_name: str = "audio") -> None:
        self._ws = None
        self._on_transcript = on_transcript
        self._source_name = source_name
        self._receive_task: asyncio.Task | None = None
        self._emitted_turn_orders: set[int] = set()

    async def connect(self) -> None:
        """Open AssemblyAI streaming WebSocket."""
        if not ASSEMBLYAI_API_KEY:
            raise RuntimeError("ASSEMBLYAI_API_KEY must be set")

        url = f"{_AAI_WS_BASE}?{urlencode(_CONNECTION_PARAMS)}"
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": ASSEMBLYAI_API_KEY},
            ssl=ssl_context,
        )

        logger.info("AssemblyAI STT connected (%s)", self._source_name)
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
                    turn_is_formatted = msg.get("turn_is_formatted", False)
                    end_of_turn = msg.get("end_of_turn", False)
                    turn_order = msg.get("turn_order")
                    if not transcript:
                        continue

                    # AssemblyAI sends progressive Turn updates. Only emit the
                    # formatted end-of-turn once so the UI/history do not get
                    # repeated partials for the same spoken turn.
                    if not (end_of_turn and turn_is_formatted):
                        continue

                    if isinstance(turn_order, int):
                        if turn_order in self._emitted_turn_orders:
                            continue
                        self._emitted_turn_orders.add(turn_order)

                    logger.info("Final turn (%s): %r", self._source_name, transcript)
                    asyncio.create_task(self._on_transcript(transcript))

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
