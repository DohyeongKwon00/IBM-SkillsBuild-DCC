import os
from dotenv import load_dotenv

load_dotenv()

# IBM watsonx.ai
WATSONX_API_KEY = os.getenv("WATSONX_API_KEY", "")
WATSONX_PROJECT_ID = os.getenv("WATSONX_PROJECT_ID", "")
WATSONX_URL = os.getenv("WATSONX_URL", "")

# IBM watsonx Orchestrate
ORCHESTRATE_URL = os.getenv("ORCHESTRATE_URL", "")
ORCHESTRATE_API_KEY = os.getenv("ORCHESTRATE_API_KEY", "")

# Agent IDs — ContextAgent is the listener; it invokes PhraseAgent + SafetyAgent
# as collaborators, so only CONTEXT_AGENT_ID is used by the server directly.
CONTEXT_AGENT_ID = os.getenv("CONTEXT_AGENT_ID", "")

# Thresholds
HESITATION_PAUSE_MS = 3000        # Server-side [pause] heartbeat threshold (ms)
HESITATION_COOLDOWN_S = 5         # Kept for client compatibility
PHRASE_AUTO_DISMISS_S = 5         # Phrase cards auto-dismiss
LATENCY_BUDGET_S = 4.0            # Target max pipeline latency (ideal)
ORCHESTRATE_TIMEOUT_S = 15.0      # ContextAgent may chain PhraseAgent+SafetyAgent — needs headroom
MIN_SPEECH_CONFIDENCE = 0.6       # Minimum Web Speech API confidence to accept transcript
TRANSCRIPT_WINDOW = 10            # Number of transcript segments to keep in sliding window
SESSION_TIMEOUT_S = 1800          # Evict sessions idle longer than this (30 min)

# Safety
MAX_SAFETY_RETRIES = 1
MIN_SAFE_PHRASES = 2

FALLBACK_PHRASES = [
    "Could you repeat that, please?",
    "Let me think about that for a moment.",
    "I understand. Thank you.",
]
