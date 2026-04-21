"""FastAPI application with WebSocket endpoint for CommCopilot.

Pipeline per final STT chunk:

    chunk
      │
      ▼
    aggregator.add_chunk()             (buffer; tentative)
      │
      ▼
    DiarizationAgent(current_window)   ──> ["student"|"other", ...]
      │
      ▼
    aggregator.record_votes(labels)
      │
      ▼
    aggregator.pop_finalized()         (chunks that exited the window)
      │
      ▼
    for each finalized chunk:
      ContextAgent("[<label>] [<ts>] <text>", context_thread_id)
        ├─ [student] → hesitation check → phrase pipeline if needed
        └─ [other]   → update role/tone only, return empty string

Chunks are serialized per session via an asyncio.Queue worker so DiarizationAgent
votes never interleave out of order.
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from commcopilot.config import (
    MIN_SPEECH_CONFIDENCE,
    PHRASE_AUTO_DISMISS_S,
    SESSION_TIMEOUT_S,
    TRANSCRIPT_WINDOW,
)
from commcopilot.diarization import LabeledChunk
from commcopilot.orchestrate import (
    call_context_listener,
    call_diarization_agent,
    warmup as orchestrate_warmup,
)
from commcopilot.session import SessionState

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

sessions: dict[str, SessionState] = {}


async def _evict_stale_sessions() -> None:
    while True:
        await asyncio.sleep(300)
        cutoff = time.monotonic() - SESSION_TIMEOUT_S
        stale = [sid for sid, s in sessions.items() if s.created_at < cutoff]
        for sid in stale:
            del sessions[sid]
        if stale:
            logger.info("Evicted %d stale sessions", len(stale))


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_evict_stale_sessions())
    yield


app = FastAPI(title="CommCopilot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def index():
    return FileResponse("frontend/index.html")


def _format_labeled_chunk(chunk: LabeledChunk) -> str:
    label = chunk.final_label or "student"
    ts_prefix = f"[{chunk.ts}] " if chunk.ts else ""
    return f"[{label}] {ts_prefix}{chunk.text}"


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    state = SessionState()
    sessions[state.session_id] = state
    logger.info(
        "Session started: %s (context_thread=%s, diarization_thread=%s)",
        state.session_id,
        state.context_thread_id,
        state.diarization_thread_id,
    )

    chunk_queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()

    async def send(msg: dict) -> None:
        await ws.send_text(json.dumps(msg))

    async def emit_log(event: dict) -> None:
        await send({"type": "log", **event})

    async def run_context(labeled_text: str) -> None:
        if state.awaiting_phrases:
            return
        state.awaiting_phrases = True
        try:
            await send({"type": "thinking"})
            phrases = await call_context_listener(
                chunk=labeled_text,
                recent_context=list(state.transcript_buffer[:-1]),
                phrases_used=list(state.phrases_used),
                on_event=emit_log,
            )
        except Exception as e:
            logger.error("listener call failed: %s", e)
            phrases = None
        finally:
            state.awaiting_phrases = False

        if phrases:
            state.hesitation_count += 1
            await send({"type": "phrases", "phrases": phrases})
        else:
            await send({"type": "idle"})

    async def process_finalized(finalized: list[LabeledChunk]) -> None:
        for chunk in finalized:
            labeled_text = _format_labeled_chunk(chunk)
            state.transcript_buffer.append(labeled_text)
            if len(state.transcript_buffer) > TRANSCRIPT_WINDOW:
                state.transcript_buffer = state.transcript_buffer[-TRANSCRIPT_WINDOW:]
            await run_context(labeled_text)

    async def diarize_one(text: str, ts: str) -> None:
        state.aggregator.add_chunk(text=text, ts=ts)
        window = state.aggregator.current_window()
        labels = await call_diarization_agent(
            window=window,
            thread_id=state.diarization_thread_id,
            on_event=emit_log,
        )
        if labels is not None:
            state.aggregator.record_votes(labels)
        finalized = state.aggregator.pop_finalized()
        if finalized:
            await process_finalized(finalized)

    async def chunk_worker() -> None:
        while True:
            item = await chunk_queue.get()
            try:
                if item is None:
                    return
                try:
                    await diarize_one(item["text"], item["ts"])
                except Exception as e:
                    logger.error("diarization pipeline failed: %s", e)
            finally:
                chunk_queue.task_done()

    worker = asyncio.create_task(chunk_worker())

    try:
        await ws.receive_text()

        asyncio.create_task(orchestrate_warmup())

        await send({
            "type": "session_ready",
            "phrase_auto_dismiss_s": PHRASE_AUTO_DISMISS_S,
            "min_speech_confidence": MIN_SPEECH_CONFIDENCE,
        })

        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "transcript":
                text = msg.get("text", "").strip()
                if not text:
                    continue
                ts = msg.get("ts", "")
                await chunk_queue.put({"text": text, "ts": ts})

            elif msg_type == "phrase_selected":
                phrase = msg.get("phrase", "")
                if phrase:
                    state.phrases_used.append(phrase)
                    logger.info("Phrase selected: %s", phrase)

            elif msg_type == "end_session":
                logger.info("Session ending: %s", state.session_id)
                await chunk_queue.join()
                remaining = state.aggregator.flush()
                if remaining:
                    await process_finalized(remaining)
                recap = (
                    f"Session complete. You hesitated {state.hesitation_count} time(s) "
                    f"and used {len(state.phrases_used)} suggested phrase(s)."
                )
                await send({
                    "type": "recap",
                    "recap": recap,
                    "phrases_used": state.phrases_used,
                })
                break

    except WebSocketDisconnect:
        logger.info("Session disconnected: %s", state.session_id)
    except Exception as e:
        logger.error("WebSocket error (%s): %s", state.session_id, e)
    finally:
        await chunk_queue.put(None)
        try:
            await asyncio.wait_for(worker, timeout=1.0)
        except (asyncio.TimeoutError, Exception):
            worker.cancel()
        sessions.pop(state.session_id, None)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["server", "commcopilot", "frontend"],
    )
