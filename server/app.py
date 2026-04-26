"""FastAPI application with WebSocket endpoint for CommCopilot.

Audio flow:
    Browser captures Speaker A and Speaker B microphones as separate
    AudioContext PCM16 streams.
    Binary frames arrive over WebSocket with a source prefix byte and are routed
    to separate AssemblyAI STT sessions.
    Each transcript is labeled by its source stream, merged into conversation
    history, and Speaker A turns are forwarded to ContextAgent for phrase help.
"""

import asyncio
import json
import logging
import re
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

SOURCES = {
    1: {"id": "speaker_a", "speaker": "Speaker A"},
    2: {"id": "speaker_b", "speaker": "Speaker B"},
}
CURRENT_USER = "Speaker A"
KNOWN_SPEAKERS = ["Speaker A", "Speaker B"]
CROSS_SOURCE_DEDUP_WINDOW_S = 1.5


def _normalize_transcript(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


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

    async def on_transcript(source_id: str, speaker: str, transcript: str) -> None:
        """Called by AssemblyAISTTClient when one source has a final transcript."""
        now = time.time()
        normalized = _normalize_transcript(transcript)
        state.recent_transcripts = [
            item for item in state.recent_transcripts
            if now - item["received_at"] <= CROSS_SOURCE_DEDUP_WINDOW_S
        ]
        if normalized and any(
            item["source"] != source_id and item["normalized"] == normalized
            for item in state.recent_transcripts
        ):
            logger.info(
                "Dropping cross-source duplicate transcript (%s): %r",
                speaker,
                transcript,
            )
            return
        state.recent_transcripts.append({
            "source": source_id,
            "normalized": normalized,
            "received_at": now,
        })

        chunk = f"[{speaker}]: {transcript}"
        state.transcript_buffer.append(chunk)
        if len(state.transcript_buffer) > TRANSCRIPT_WINDOW:
            state.transcript_buffer = state.transcript_buffer[-TRANSCRIPT_WINDOW:]
        await send({
            "type": "transcript",
            "text": chunk,
            "speaker": speaker,
            "source": source_id,
            "received_at": now,
        })
        if speaker == CURRENT_USER:
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
                conversation_history=list(state.transcript_buffer),
                current_user=CURRENT_USER,
                ai_solution_user=CURRENT_USER,
                known_speakers=KNOWN_SPEAKERS,
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

    stt_clients: dict[int, AssemblyAISTTClient] = {}

    try:
        # Wait for the browser's initial "start" message.
        await ws.receive_text()

        asyncio.create_task(orchestrate_warmup())

        if not ASSEMBLYAI_API_KEY:
            await send({"type": "error", "message": "AssemblyAI API key is not configured on the server."})
            return

        try:
            for source_code, source in SOURCES.items():
                async def source_callback(
                    transcript: str,
                    source_id: str = source["id"],
                    speaker: str = source["speaker"],
                ) -> None:
                    await on_transcript(source_id, speaker, transcript)

                stt_client = AssemblyAISTTClient(
                    on_transcript=source_callback,
                    source_name=source["speaker"],
                )
                await stt_client.connect()
                stt_clients[source_code] = stt_client
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
                # First byte identifies the source stream; remaining bytes are PCM16.
                payload = data["bytes"]
                if not payload:
                    continue
                source_code = payload[0]
                audio = payload[1:]
                stt_client = stt_clients.get(source_code)
                if stt_client:
                    await stt_client.send_audio(audio)

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
        for stt_client in stt_clients.values():
            await stt_client.close()
        sessions.pop(state.session_id, None)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.app:app", host="0.0.0.0", port=8000, reload=True)
