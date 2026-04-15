"""FastAPI application with WebSocket endpoint for CommCopilot (listener mode).

In listener mode the browser no longer decides when to trigger — it only
transcribes speech and streams final STT chunks to the server. The server
forwards each chunk as a silent message to ContextAgent, which decides on its
own whether to stay silent or fire the phrase pipeline.

A background heartbeat injects a "[pause]" marker chunk when the student has
been silent for longer than HESITATION_PAUSE_MS, so prolonged silence is also
visible to the listener agent.
"""

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from commcopilot.config import (
    HESITATION_COOLDOWN_S,
    HESITATION_PAUSE_MS,
    MIN_SPEECH_CONFIDENCE,
    PHRASE_AUTO_DISMISS_S,
    SESSION_TIMEOUT_S,
    TRANSCRIPT_WINDOW,
)
from commcopilot.orchestrate import call_context_listener, warmup as orchestrate_warmup
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


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    state = SessionState()
    sessions[state.session_id] = state
    logger.info("Session started: %s (thread_id=%s)", state.session_id, state.thread_id)

    async def send(msg: dict) -> None:
        await ws.send_text(json.dumps(msg))

    async def emit_log(event: dict) -> None:
        await send({"type": "log", **event})

    async def run_listener(chunk: str, is_pause: bool) -> None:
        """Send one chunk to ContextAgent; push phrases to client if returned."""
        if state.awaiting_phrases:
            return
        state.awaiting_phrases = True
        try:
            if is_pause:
                await send({
                    "type": "log",
                    "stage": "listener",
                    "status": "pause_heartbeat",
                    "detail": "injecting [pause] marker",
                })
            await send({"type": "thinking"})

            phrases = await call_context_listener(
                chunk=chunk,
                thread_id=state.thread_id,
                phrases_used=list(state.phrases_used),
                is_pause=is_pause,
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

    async def pause_heartbeat() -> None:
        """If student is silent for HESITATION_PAUSE_MS, let ContextAgent know."""
        pause_seconds = HESITATION_PAUSE_MS / 1000.0
        while True:
            await asyncio.sleep(pause_seconds)
            if state.awaiting_phrases:
                continue
            idle_for = time.monotonic() - state.last_transcript_at
            if idle_for >= pause_seconds:
                # Reset the clock so we don't fire again on the next tick.
                state.last_transcript_at = time.monotonic()
                asyncio.create_task(run_listener("[pause]", is_pause=True))

    heartbeat_task: asyncio.Task | None = None

    try:
        # Wait for the client's start signal (or any first message) before
        # kicking off the session; ignore its content — no scenario to read.
        await ws.receive_text()

        asyncio.create_task(orchestrate_warmup())

        await send({
            "type": "session_ready",
            "phrase_auto_dismiss_s": PHRASE_AUTO_DISMISS_S,
            "min_speech_confidence": MIN_SPEECH_CONFIDENCE,
            "hesitation_cooldown_s": HESITATION_COOLDOWN_S,
            "hesitation_pause_ms": HESITATION_PAUSE_MS,
        })
        state.last_transcript_at = time.monotonic()
        heartbeat_task = asyncio.create_task(pause_heartbeat())

        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "transcript":
                text = msg.get("text", "").strip()
                if not text:
                    continue
                state.transcript_buffer.append(text)
                if len(state.transcript_buffer) > TRANSCRIPT_WINDOW:
                    state.transcript_buffer = state.transcript_buffer[-TRANSCRIPT_WINDOW:]
                state.last_transcript_at = time.monotonic()
                asyncio.create_task(run_listener(text, is_pause=False))

            elif msg_type == "phrase_selected":
                phrase = msg.get("phrase", "")
                if phrase:
                    state.phrases_used.append(phrase)
                    logger.info("Phrase selected: %s", phrase)

            elif msg_type == "end_session":
                logger.info("Session ending: %s", state.session_id)
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
        if heartbeat_task:
            heartbeat_task.cancel()
        sessions.pop(state.session_id, None)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.app:app", host="0.0.0.0", port=8000, reload=True)
