"""IBM watsonx Orchestrate interface for CommCopilot.

Authentication flow:
  IAM API key -> POST iam.cloud.ibm.com/identity/token -> access_token (1h TTL)
  access_token used as Bearer in every Orchestrate API call

Two call paths:
  call_supervisor_agent()    -- POST to SupervisorAgent (chains collaborators internally)
  call_pipeline_sequential() -- 3 sequential calls: ContextAgent -> PhraseAgent -> SafetyAgent

USE_SUPERVISOR env flag (config.py) picks which path server/app.py uses.

API endpoint format:
  POST {ORCHESTRATE_URL}/api/v1/orchestrate/{agent_id}/chat/completions
  Response: OpenAI-compatible {"choices": [{"message": {"content": "..."}}]}
"""

import json
import logging
import re
import time
from typing import Optional

import httpx

from commcopilot.config import (
    CONTEXT_AGENT_ID,
    FALLBACK_PHRASES,
    ORCHESTRATE_API_KEY,
    ORCHESTRATE_TIMEOUT_S,
    ORCHESTRATE_URL,
    PHRASE_AGENT_ID,
    SAFETY_AGENT_ID,
    SCENARIOS,
    SUPERVISOR_AGENT_ID,
)

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


def _strip_fences(raw: str) -> str:
    return _FENCE_RE.sub("", raw).rstrip("`").strip()


async def _get_iam_token() -> str:
    """Exchange IAM API key for an access token. Caches the token until 5 min before expiry."""
    global _iam_token_cache
    token, expires_at = _iam_token_cache

    if token and time.time() < expires_at - 300:  # 5-min buffer
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
        logger.debug("IAM token refreshed, expires in %ds", data.get("expires_in", 3600))
        return token


async def _chat(agent_id: str, prompt: str, warmup: bool = False) -> str:
    """POST a message to one Orchestrate agent and return the response text.

    Uses OpenAI-compatible /chat/completions endpoint.
    """
    if not ORCHESTRATE_URL:
        raise OrchestrateError("ORCHESTRATE_URL is not configured")
    if not agent_id:
        raise OrchestrateError("agent_id is empty — run 'orchestrate agents list' and set env vars")

    url = f"{ORCHESTRATE_URL}/api/v1/orchestrate/{agent_id}/chat/completions"
    token = await _get_iam_token()
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

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


def _scenario_context(scenario: str) -> str:
    return SCENARIOS.get(scenario, SCENARIOS["office_hours"])["system_context"]


def _transcript_text(transcript_buffer: list[str]) -> str:
    return "\n".join(transcript_buffer) if transcript_buffer else "(no transcript yet)"


async def call_supervisor_agent(
    scenario: str,
    transcript_buffer: list[str],
    phrases_used: list[str],
    warmup: bool = False,
) -> list[str]:
    """Call SupervisorAgent. It chains ContextAgent->PhraseAgent->SafetyAgent via collaborators.

    Returns a list of 2-3 safe phrase strings.
    On warmup=True swallows the result (cold-start mitigation only).
    """
    context = _scenario_context(scenario)
    transcript = _transcript_text(transcript_buffer)
    used_hint = (
        f"Phrases already used (avoid repeating): {', '.join(phrases_used[-5:])}"
        if phrases_used
        else ""
    )
    prompt = (
        f"Scenario: {context}\n"
        f"Transcript:\n{transcript}\n"
        f"{used_hint}\n\n"
        "Use ContextAgent to understand the situation, then PhraseAgent to generate 3 short phrases "
        "the student can say next, then SafetyAgent to verify them. "
        "Return ONLY a JSON array of 2-3 phrase strings. No markdown fences, no explanation."
    )

    raw = await _chat(SUPERVISOR_AGENT_ID, prompt, warmup=warmup)

    if warmup:
        return []

    try:
        phrases = json.loads(_strip_fences(raw))
        if isinstance(phrases, list) and phrases:
            return [str(p) for p in phrases[:3]]
        raise OrchestrateParseError(f"Unexpected shape: {raw!r}")
    except json.JSONDecodeError as e:
        raise OrchestrateParseError(f"JSON parse failed: {raw!r}") from e


async def call_pipeline_sequential(
    scenario: str,
    transcript_buffer: list[str],
    phrases_used: list[str],
) -> list[str]:
    """Fallback: 3 sequential calls to individual agents (USE_SUPERVISOR=false)."""
    context = _scenario_context(scenario)
    transcript = _transcript_text(transcript_buffer)

    # Step 1: ContextAgent
    ctx_prompt = (
        f"Scenario: {context}\n"
        f"Transcript:\n{transcript}\n\n"
        'Return ONLY JSON: {"role": "...", "tone": "...", "intent": "..."}. No fences.'
    )
    ctx_raw = await _chat(CONTEXT_AGENT_ID, ctx_prompt)
    try:
        ctx = json.loads(_strip_fences(ctx_raw))
    except Exception:
        ctx = {"role": "professor", "tone": "formal", "intent": "continue conversation"}

    # Step 2: PhraseAgent
    used_hint = (
        f"Avoid repeating: {', '.join(phrases_used[-5:])}" if phrases_used else ""
    )
    phrase_prompt = (
        f"Talking to: {ctx.get('role', 'person')}, tone: {ctx.get('tone', 'formal')}, "
        f"intent: {ctx.get('intent', 'continue conversation')}.\n"
        f"Transcript:\n{transcript}\n"
        f"{used_hint}\n\n"
        "Generate 3 short phrases (under 15 words each) the student can say next. "
        "Return ONLY a JSON array. No fences."
    )
    phrase_raw = await _chat(PHRASE_AGENT_ID, phrase_prompt)
    try:
        phrases: list[str] = json.loads(_strip_fences(phrase_raw))
        if not isinstance(phrases, list):
            raise ValueError
    except Exception:
        return list(FALLBACK_PHRASES)

    # Step 3: SafetyAgent
    safety_prompt = (
        f"Check these phrases for profanity or inappropriate content: {json.dumps(phrases)}\n"
        "Return ONLY a JSON array of safe phrases. "
        f"If fewer than 2 are safe, return {json.dumps(list(FALLBACK_PHRASES))}. No fences."
    )
    safety_raw = await _chat(SAFETY_AGENT_ID, safety_prompt)
    try:
        safe: list[str] = json.loads(_strip_fences(safety_raw))
        if isinstance(safe, list) and safe:
            return [str(p) for p in safe[:3]]
    except Exception:
        pass

    return phrases[:3] if phrases else list(FALLBACK_PHRASES)


async def get_phrases(
    scenario: str,
    transcript_buffer: list[str],
    phrases_used: list[str],
    use_supervisor: bool = True,
) -> list[str]:
    """Entry point called by server/app.py. Always returns a non-empty list."""
    try:
        if use_supervisor:
            return await call_supervisor_agent(scenario, transcript_buffer, phrases_used)
        else:
            return await call_pipeline_sequential(scenario, transcript_buffer, phrases_used)
    except OrchestrateTimeoutError:
        logger.warning("Orchestrate timed out, returning fallback phrases")
    except OrchestrateParseError as e:
        logger.warning("Orchestrate parse error: %s", e)
    except OrchestrateError as e:
        logger.error("Orchestrate error: %s", e)
    except Exception as e:
        logger.error("Unexpected error: %s", e)

    return list(FALLBACK_PHRASES)


async def warmup(scenario: str) -> None:
    """Fire a cheap warm-up call to avoid IAM token + cold-start latency on first hesitation."""
    if not ORCHESTRATE_URL or not ORCHESTRATE_API_KEY:
        logger.debug("Warm-up skipped: ORCHESTRATE_URL or ORCHESTRATE_API_KEY not set")
        return
    agent_id = SUPERVISOR_AGENT_ID if SUPERVISOR_AGENT_ID else CONTEXT_AGENT_ID
    if not agent_id:
        logger.debug("Warm-up skipped: no agent IDs configured yet")
        return
    try:
        # Pre-fetch the IAM token so first real call is instant
        await _get_iam_token()
        logger.info("IAM token warmed up for scenario: %s", scenario)
    except Exception as e:
        logger.warning("Warm-up failed (non-fatal): %s", e)
