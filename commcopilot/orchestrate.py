"""IBM watsonx Orchestrate interface for CommCopilot (listener mode).

Authentication flow:
  IAM API key -> POST iam.cloud.ibm.com/identity/token -> access_token (1h TTL)
  access_token used as Bearer in every Orchestrate API call

Call path:
  call_context_listener()  -- POSTs a silent STT chunk to ContextAgent, which
                              accumulates conversation context via thread_id.
                              ContextAgent's guidelines decide whether to stay
                              silent or invoke PhraseAgent+SafetyAgent
                              collaborators and return phrases.

API endpoint (IBM Cloud SaaS):
  POST {ORCHESTRATE_URL}/v1/orchestrate/{agent_id}/chat/completions
  Header X-IBM-THREAD-ID: <uuid>      (keeps the conversation thread alive)
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
    FALLBACK_PHRASES,
    ORCHESTRATE_API_KEY,
    ORCHESTRATE_TIMEOUT_S,
    ORCHESTRATE_URL,
)

EventCallback = Optional[Callable[[dict], Awaitable[None]]]

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```[a-z]*\n?", re.MULTILINE)

# IAM token cache: (token_str, expires_at_epoch)
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
    """Exchange IAM API key for an access token. Cached until 5 min before expiry."""
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
    """POST a message to one Orchestrate agent and return the response text.

    When `thread_id` is provided it is sent as the X-IBM-THREAD-ID header so
    the agent accumulates conversation history across successive calls.
    """
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
    """ContextAgent guideline 1 says 'return empty' when the student is fluent.

    Treat empty, whitespace-only, and one-word acknowledgements as silent.
    """
    text = (raw or "").strip().strip("`").strip()
    if not text:
        return True
    # Sometimes the LLM still emits a single filler token like "ok" / "."
    if len(text) <= 2:
        return True
    return False


async def call_context_listener(
    chunk: str,
    thread_id: str,
    phrases_used: list[str],
    is_pause: bool = False,
    on_event: EventCallback = None,
) -> Optional[list[str]]:
    """Send one STT chunk to ContextAgent as a silent listener message.

    ContextAgent infers role/tone/intent from the running thread itself — no
    scenario context is provided by the server.

    Returns:
        - list[str] of 2-3 safe phrases if ContextAgent's "hesitation" guideline
          fires and it runs the Phrase/Safety collaborators.
        - None if ContextAgent decides to stay silent (no hesitation).
    """
    used_hint = (
        f"Phrases the student already used (avoid suggesting these again): "
        f"{', '.join(phrases_used[-5:])}"
        if phrases_used
        else ""
    )
    marker = "[pause]" if is_pause else chunk

    prompt = (
        f"New transcript chunk from live conversation: {marker}\n"
        f"{used_hint}\n\n"
        "Apply your guidelines. Infer situation, role, user goal, tone, "
        "formality, intent, confidence, and hesitation_type from the running "
        "thread. If the student is speaking fluently, return an empty string. "
        "If the student is hesitating, default to response-support: help "
        "complete the student's unfinished thought, continue their intended "
        "answer, or answer the current question naturally. Do not default to "
        "generic apology or repeat-request phrases. Use repeat-request phrases "
        "only when there is strong evidence the student did not hear or "
        "understand the other speaker. Then invoke phrase_generation_agent and "
        "safety_filter_agent and return ONLY a JSON array of 2-3 safe phrase "
        "strings."
    )

    await _emit(on_event, {
        "stage": "context_agent",
        "status": "calling",
        "detail": "listener chunk" + (" [pause]" if is_pause else ""),
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
    """Pre-fetch the IAM token so the first real call doesn't pay for it."""
    if not ORCHESTRATE_URL or not ORCHESTRATE_API_KEY:
        return
    try:
        await _get_iam_token()
        logger.info("IAM token warmed up")
    except Exception as e:
        logger.warning("Warm-up failed (non-fatal): %s", e)
