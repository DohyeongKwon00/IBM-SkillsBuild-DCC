"""IBM watsonx Orchestrate interface for CommCopilot.

Authentication flow:
  IAM API key -> POST iam.cloud.ibm.com/identity/token -> access_token (1h TTL)
  access_token used as Bearer in every Orchestrate API call

Call path:
  call_context_listener()  -- POSTs each STT chunk to ContextAgent via thread_id.
                              ContextAgent decides whether to stay silent or
                              return 2-3 phrase suggestions.

API endpoint (IBM Cloud SaaS):
  POST {ORCHESTRATE_URL}/v1/orchestrate/{agent_id}/chat/completions
  Header X-IBM-THREAD-ID: <uuid>
  Response: OpenAI-compatible {"choices": [{"message": {"content": "..."}}]}
"""

import json
import logging
import re
import time
from typing import Awaitable, Callable, Optional

import httpx

from commcopilot.config import (
    CONTEXT_AGENT_ID,
    ORCHESTRATE_API_KEY,
    ORCHESTRATE_TIMEOUT_S,
    ORCHESTRATE_URL,
)

EventCallback = Optional[Callable[[dict], Awaitable[None]]]

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```[a-z]*\n?", re.MULTILINE)

_iam_token_cache: tuple[str, float] = ("", 0.0)


class OrchestrateError(Exception):
    pass


class OrchestrateTimeoutError(OrchestrateError):
    pass


class OrchestrateParseError(OrchestrateError):
    pass


async def _emit(on_event: EventCallback, event: dict) -> None:
    if on_event is None:
        return
    try:
        await on_event(event)
    except Exception as e:
        logger.debug("on_event callback failed: %s", e)


def _strip_fences(raw: str) -> str:
    return _FENCE_RE.sub("", raw).rstrip("`").strip()


async def _get_iam_token() -> str:
    global _iam_token_cache
    token, expires_at = _iam_token_cache

    if token and time.time() < expires_at - 300:
        return token

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://iam.cloud.ibm.com/identity/token",
            data={
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": ORCHESTRATE_API_KEY,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        expires_at = time.time() + data.get("expires_in", 3600)
        _iam_token_cache = (token, expires_at)
        return token


async def _chat(
    agent_id: str,
    prompt: str,
    thread_id: Optional[str] = None,
    warmup: bool = False,
) -> str:
    if not ORCHESTRATE_URL:
        raise OrchestrateError("ORCHESTRATE_URL is not configured")
    if not agent_id:
        raise OrchestrateError("agent_id is empty — run 'orchestrate agents list' and set env vars")

    url = f"{ORCHESTRATE_URL}/v1/orchestrate/{agent_id}/chat/completions"
    token = await _get_iam_token()
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if thread_id:
        headers["X-IBM-THREAD-ID"] = thread_id

    try:
        async with httpx.AsyncClient(timeout=ORCHESTRATE_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except httpx.TimeoutException as e:
        if warmup:
            return ""
        raise OrchestrateTimeoutError(f"Agent {agent_id} timed out") from e
    except Exception as e:
        if warmup:
            logger.warning("Warm-up call failed (non-fatal): %s", e)
            return ""
        raise OrchestrateError(f"Agent {agent_id} call failed: {e}") from e


def _is_silent_response(raw: str) -> bool:
    text = (raw or "").strip().strip("`").strip()
    if not text:
        return True
    if len(text) <= 2:
        return True
    return False


async def call_context_listener(
    chunk: str,
    thread_id: str,
    phrases_used: list[str],
    conversation_history: Optional[list[str]] = None,
    current_user: str = "Speaker A",
    ai_solution_user: str = "Speaker A",
    known_speakers: Optional[list[str]] = None,
    on_event: EventCallback = None,
) -> Optional[list[str]]:
    """Send one STT chunk to ContextAgent.

    Returns list[str] of 2-3 phrases if hesitation detected, None otherwise.
    """
    used_hint = (
        f"Phrases the student already used (avoid suggesting these again): "
        f"{', '.join(phrases_used[-5:])}"
        if phrases_used
        else ""
    )

    speakers = known_speakers or ["Speaker A", "Speaker B"]
    history = "\n".join((conversation_history or [])[-10:])
    prompt = (
        f"current_user: {current_user}\n"
        f"ai_solution_user: {ai_solution_user}\n"
        f"known_speakers: {json.dumps(speakers)}\n"
        f"Recent conversation:\n{history}\n"
        f"Transcript chunk: {chunk}\n"
        f"{used_hint}\n"
        "Only generate suggestions for Speaker A. Speaker B's speech is context only. "
        "Apply your guidelines and return empty string or JSON array of 2-3 phrases."
    )

    await _emit(on_event, {
        "stage": "context_agent",
        "status": "calling",
        "prompt": prompt,
    })

    try:
        raw = await _chat(CONTEXT_AGENT_ID, prompt, thread_id=thread_id)
    except OrchestrateTimeoutError:
        await _emit(on_event, {"stage": "context_agent", "status": "timeout"})
        return None
    except OrchestrateError as e:
        await _emit(on_event, {"stage": "context_agent", "status": "error", "detail": str(e)})
        return None

    await _emit(on_event, {"stage": "context_agent", "status": "responded", "output": raw})

    if _is_silent_response(raw):
        await _emit(on_event, {"stage": "context_agent", "status": "silent", "detail": "no hesitation"})
        return None

    stripped = _strip_fences(raw)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        await _emit(on_event, {
            "stage": "context_agent",
            "status": "parse_failed",
            "detail": "treating as silent",
        })
        return None

    if isinstance(parsed, list) and parsed:
        phrases = [str(p) for p in parsed[:3]]
        await _emit(on_event, {"stage": "context_agent", "status": "parsed", "phrases": phrases})
        return phrases

    await _emit(on_event, {"stage": "context_agent", "status": "unexpected_shape"})
    return None


async def warmup() -> None:
    if not ORCHESTRATE_URL or not ORCHESTRATE_API_KEY:
        return
    try:
        await _get_iam_token()
        logger.info("IAM token warmed up")
    except Exception as e:
        logger.warning("Warm-up failed (non-fatal): %s", e)
