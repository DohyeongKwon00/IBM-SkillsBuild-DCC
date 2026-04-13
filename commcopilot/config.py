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

# Agent IDs — fill in after: orchestrate agents import + orchestrate agents list
SUPERVISOR_AGENT_ID = os.getenv("SUPERVISOR_AGENT_ID", "")
CONTEXT_AGENT_ID = os.getenv("CONTEXT_AGENT_ID", "")
PHRASE_AGENT_ID = os.getenv("PHRASE_AGENT_ID", "")
SAFETY_AGENT_ID = os.getenv("SAFETY_AGENT_ID", "")

# Pipeline routing
USE_SUPERVISOR = os.getenv("USE_SUPERVISOR", "true").lower() == "true"

# Thresholds
HESITATION_PAUSE_MS = 1500        # Browser silence detection threshold (ms)
HESITATION_COOLDOWN_S = 5         # Debounce: ignore hesitations for this long after one triggers
PHRASE_AUTO_DISMISS_S = 5         # Phrase cards auto-dismiss
LATENCY_BUDGET_S = 4.0            # Target max pipeline latency
ORCHESTRATE_TIMEOUT_S = 6.0       # Timeout for Orchestrate REST calls
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

# Scenarios
SCENARIOS = {
    "office_hours": {
        "name": "Office Hours with Professor",
        "default_role": "professor",
        "default_tone": "formal",
        "system_context": (
            "The student is in a professor's office during office hours. "
            "The conversation is academic and formal. The professor discusses "
            "deadlines, assignments, grades, or course material."
        ),
    },
    "admin_office": {
        "name": "Admin Office Interaction",
        "default_role": "admin_staff",
        "default_tone": "semi-formal",
        "system_context": (
            "The student is at a university administrative office. "
            "The conversation is semi-formal and procedural, covering topics like "
            "enrollment, housing, financial aid, or document requests."
        ),
    },
}
