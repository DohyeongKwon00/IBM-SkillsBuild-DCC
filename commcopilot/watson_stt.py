"""IBM Watson Speech to Text WebSocket client (per-session).

Audio flow:
    Browser (MediaRecorder, webm/opus, 250ms chunks)
    -> binary WebSocket frames -> FastAPI
    -> WatsonSTTClient.send_audio()
    -> Watson STT WebSocket (wss://)
    -> final transcripts + speaker_labels
    -> on_transcript callback("[Speaker N]: text")

State machine in _receive_loop():
    1. final result arrives  -> store in _pending_transcripts[result_index]
                             -> schedule 2s fallback task (emits with Speaker 0)
    2. speaker_labels arrive -> pop oldest pending, cancel its fallback,
                                determine dominant speaker, emit chunk

Watson sends speaker_labels after the corresponding final transcript, so FIFO
(pop by min key) is safe under normal ordering. The 2s fallback ensures a chunk
is always emitted even when speaker_labels are delayed or absent (e.g. single
speaker, short session).
"""

import asyncio
import base64
import json
import logging
from typing import Awaitable, Callable

import websockets
import websockets.exceptions

from commcopilot.config import WATSON_STT_API_KEY, WATSON_STT_MODEL, WATSON_STT_URL

logger = logging.getLogger(__name__)

OnTranscript = Callable[[str], Awaitable[None]]

_SPEAKER_LABEL_TIMEOUT_S = 2.0


class WatsonSTTClient:
    """Manages a single Watson STT WebSocket connection for one user session."""

    def __init__(self, on_transcript: OnTranscript) -> None:
        self._ws = None
        self._on_transcript = on_transcript
        # Maps result_index -> transcript text for final results awaiting speaker_labels.
        self._pending_transcripts: dict[int, str] = {}
        # Maps result_index -> fallback asyncio.Task (cancelled if speaker_labels arrive).
        self._fallback_tasks: dict[int, asyncio.Task] = {}
        self._receive_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Open Watson STT WebSocket and confirm listening state."""
        if not WATSON_STT_API_KEY or not WATSON_STT_URL:
            raise RuntimeError("WATSON_STT_API_KEY and WATSON_STT_URL must be set")

        # WATSON_STT_URL may be the full REST URL from IBM Cloud Console
        # (e.g. "https://api.us-south.speech-to-text.watson.cloud.ibm.com/instances/...")
        # or just the hostname. Extract the hostname in either case.
        from urllib.parse import urlparse
        parsed = urlparse(WATSON_STT_URL if WATSON_STT_URL.startswith("http") else f"https://{WATSON_STT_URL}")
        # model must be a URL query parameter — Watson ignores it in the JSON start message
        wss_url = f"wss://{parsed.netloc}/v1/recognize?model={WATSON_STT_MODEL}"
        credentials = base64.b64encode(
            f"apikey:{WATSON_STT_API_KEY}".encode()
        ).decode()

        self._ws = await websockets.connect(
            wss_url,
            additional_headers={"Authorization": f"Basic {credentials}"},
        )

        await self._ws.send(json.dumps({
            "action": "start",
            "content-type": "audio/webm;codecs=opus",
            "interim_results": True,
            "speaker_labels": True,
        }))

        # Wait for Watson to confirm it is ready (timeout: 5s).
        state_msg = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
        data = json.loads(state_msg)
        if data.get("state") != "listening":
            await self._ws.close()
            raise RuntimeError(f"Watson STT did not enter listening state: {data}")

        logger.info("Watson STT connected and listening")

        # Start background loop to receive transcripts.
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def send_audio(self, data: bytes) -> None:
        """Forward a binary audio frame from the browser to Watson STT."""
        if self._ws:
            try:
                await self._ws.send(data)
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Tried to send audio after Watson STT closed")

    async def _emit_fallback(self, idx: int) -> None:
        """Emit a pending transcript with Speaker 0 if speaker_labels never arrived."""
        await asyncio.sleep(_SPEAKER_LABEL_TIMEOUT_S)
        transcript = self._pending_transcripts.pop(idx, None)
        self._fallback_tasks.pop(idx, None)
        if transcript:
            logger.info("Speaker labels timeout — emitting with Speaker 0: %r", transcript)
            asyncio.create_task(self._on_transcript(f"[Speaker 0]: {transcript}"))

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Step 1: store final transcripts keyed by result_index.
                if "results" in msg:
                    for result in msg["results"]:
                        if result.get("final"):
                            idx = msg.get("result_index", 0)
                            text = result["alternatives"][0]["transcript"].strip()
                            logger.info("Watson final transcript [idx=%d]: %r", idx, text)
                            if text:
                                self._pending_transcripts[idx] = text
                                # Schedule fallback in case speaker_labels never arrive.
                                self._fallback_tasks[idx] = asyncio.create_task(
                                    self._emit_fallback(idx)
                                )

                # Step 2: when speaker_labels are finalized, match to oldest pending transcript.
                if "speaker_labels" in msg:
                    labels = msg["speaker_labels"]
                    if labels and labels[-1].get("final") and self._pending_transcripts:
                        idx = min(self._pending_transcripts.keys())
                        transcript = self._pending_transcripts.pop(idx)
                        # Cancel the fallback for this index.
                        task = self._fallback_tasks.pop(idx, None)
                        if task:
                            task.cancel()
                        speaker_id = _dominant_speaker(labels)
                        chunk = f"[Speaker {speaker_id}]: {transcript}"
                        logger.info("Emitting chunk: %r", chunk)
                        asyncio.create_task(self._on_transcript(chunk))

        except websockets.exceptions.ConnectionClosed:
            logger.info("Watson STT connection closed")
        except Exception as e:
            logger.error("Watson STT receive loop error: %s", e)

    async def close(self) -> None:
        """Stop recording and close the Watson STT connection."""
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        for task in self._fallback_tasks.values():
            task.cancel()
        self._fallback_tasks.clear()
        if self._ws:
            try:
                await self._ws.send(json.dumps({"action": "stop"}))
                await self._ws.close()
            except Exception:
                pass


def _dominant_speaker(speaker_labels: list[dict]) -> int:
    """Return the speaker ID that covers the most time in the utterance."""
    if not speaker_labels:
        return 0
    speaker_time: dict[int, float] = {}
    for lbl in speaker_labels:
        spk = lbl["speaker"]
        speaker_time[spk] = speaker_time.get(spk, 0.0) + (lbl["to"] - lbl["from"])
    return max(speaker_time, key=speaker_time.get)
