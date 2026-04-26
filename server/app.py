"""FastAPI application with WebSocket endpoint for CommCopilot.

Audio flow:
    Browser captures mic audio via AudioContext (PCM16, 16 kHz chunks).
    Binary frames arrive over WebSocket and are forwarded to AssemblyAI STT.
    AssemblyAI returns speaker-labeled transcripts, e.g. "[Speaker A]: I was wondering..."
    Each final transcript is forwarded to ContextAgent (via Orchestrate), which
    detects hesitation and returns phrase suggestions when the student is struggling.
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from commcopilot.config import (
    ASSEMBLYAI_API_KEY,
    PHRASE_AUTO_DISMISS_S,
    SESSION_TIMEOUT_S,
    TRANSCRIPT_WINDOW,
)
from commcopilot.orchestrate import call_context_listener, warmup as orchestrate_warmup
from commcopilot.session import SessionState
from commcopilot.assemblyai_stt import AssemblyAISTTClient

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
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            pass

    async def emit_log(event: dict) -> None:
        await send({"type": "log", **event})

    async def on_transcript(chunk: str) -> None:
        """Called by AssemblyAISTTClient when a speaker-labeled transcript is ready."""
        state.transcript_buffer.append(chunk)
        if len(state.transcript_buffer) > TRANSCRIPT_WINDOW:
            state.transcript_buffer = state.transcript_buffer[-TRANSCRIPT_WINDOW:]
        await send({"type": "transcript", "text": chunk})
        asyncio.create_task(run_listener(chunk))

    async def run_listener(chunk: str) -> None:
        if state.awaiting_phrases:
            return
        state.awaiting_phrases = True
        try:
            await send({"type": "thinking"})
            phrases = await call_context_listener(
                chunk=chunk,
                thread_id=state.thread_id,
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

    stt: AssemblyAISTTClient | None = None

    try:
        # Wait for the browser's initial "start" message.
        await ws.receive_text()

        asyncio.create_task(orchestrate_warmup())

        if not ASSEMBLYAI_API_KEY:
            await send({"type": "error", "message": "AssemblyAI API key is not configured on the server."})
            return

        stt = AssemblyAISTTClient(on_transcript=on_transcript)
        try:
            await stt.connect()
        except Exception as e:
            logger.error("AssemblyAI STT connect failed: %s", e)
            await send({"type": "error", "message": f"Could not connect to AssemblyAI STT: {e}"})
            return

        await send({
            "type": "session_ready",
            "phrase_auto_dismiss_s": PHRASE_AUTO_DISMISS_S,
        })

        while True:
            # Receive either binary audio frames or text control messages.
            data = await ws.receive()

            if data.get("bytes"):
                # Binary PCM16 frame from browser AudioContext -> AssemblyAI.
                await stt.send_audio(data["bytes"])

            elif data.get("text"):
                msg = json.loads(data["text"])
                msg_type = msg.get("type")

                if msg_type == "phrase_selected":
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
        if stt:
            await stt.close()
        sessions.pop(state.session_id, None)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.app:app", host="0.0.0.0", port=8000, reload=True)
