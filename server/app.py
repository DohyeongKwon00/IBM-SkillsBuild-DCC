"""FastAPI application with WebSocket endpoint for CommCopilot."""

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
    FALLBACK_PHRASES,
    HESITATION_COOLDOWN_S,
    MIN_SPEECH_CONFIDENCE,
    PHRASE_AUTO_DISMISS_S,
    SCENARIOS,
    SESSION_TIMEOUT_S,
    TRANSCRIPT_WINDOW,
    USE_SUPERVISOR,
)
from commcopilot.orchestrate import get_phrases, warmup as orchestrate_warmup
from commcopilot.session import SessionState

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# Module-level session registry
sessions: dict[str, SessionState] = {}


async def _evict_stale_sessions() -> None:
    """Background task: remove sessions inactive for SESSION_TIMEOUT_S."""
    while True:
        await asyncio.sleep(300)  # check every 5 minutes
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


@app.get("/api/scenarios")
async def get_scenarios():
    return {key: {"name": val["name"]} for key, val in SCENARIOS.items()}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    session_id = str(uuid.uuid4())
    state = SessionState(session_id=session_id)
    sessions[session_id] = state
    logger.info("Session started: %s", session_id)

    async def send(msg: dict) -> None:
        await ws.send_text(json.dumps(msg))

    try:
        # Wait for scenario selection before doing anything
        raw = await ws.receive_text()
        msg = json.loads(raw)
        if msg.get("type") == "scenario":
            state.scenario = msg.get("scenario", "office_hours")
            logger.info("Scenario set: %s (session %s)", state.scenario, session_id)

        # Warm up Orchestrate to avoid cold-start on first hesitation
        asyncio.create_task(orchestrate_warmup(state.scenario))

        # Confirm session is ready
        await send({
            "type": "session_ready",
            "phrase_auto_dismiss_s": PHRASE_AUTO_DISMISS_S,
            "min_speech_confidence": MIN_SPEECH_CONFIDENCE,
            "hesitation_cooldown_s": HESITATION_COOLDOWN_S,
        })

        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "transcript":
                text = msg.get("text", "").strip()
                if text:
                    state.transcript_buffer.append(text)
                    # Sliding window: keep last TRANSCRIPT_WINDOW segments
                    if len(state.transcript_buffer) > TRANSCRIPT_WINDOW:
                        state.transcript_buffer = state.transcript_buffer[-TRANSCRIPT_WINDOW:]

            elif msg_type == "hesitation":
                trigger = msg.get("trigger", "pause")  # "pause" or "filler"
                if state.awaiting_phrases:
                    logger.debug(
                        "Hesitation ignored (awaiting_phrases=True): %s", session_id
                    )
                    continue

                logger.info(
                    "Hesitation (%s) detected (session %s)", trigger, session_id
                )
                state.hesitation_count += 1
                state.awaiting_phrases = True

                await send({"type": "thinking"})

                try:
                    phrases = await get_phrases(
                        scenario=state.scenario,
                        transcript_buffer=list(state.transcript_buffer),
                        phrases_used=list(state.phrases_used),
                        use_supervisor=USE_SUPERVISOR,
                    )
                except Exception as e:
                    logger.error("get_phrases failed: %s", e)
                    phrases = list(FALLBACK_PHRASES)
                finally:
                    state.awaiting_phrases = False

                await send({"type": "phrases", "phrases": phrases})

            elif msg_type == "phrase_selected":
                phrase = msg.get("phrase", "")
                if phrase:
                    state.phrases_used.append(phrase)
                    logger.info("Phrase selected: %s (session %s)", phrase, session_id)

            elif msg_type == "end_session":
                logger.info("Session ending: %s", session_id)
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
        logger.info("Session disconnected: %s", session_id)
    except Exception as e:
        logger.error("WebSocket error (session %s): %s", session_id, e)
    finally:
        sessions.pop(session_id, None)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.app:app", host="0.0.0.0", port=8000, reload=True)
