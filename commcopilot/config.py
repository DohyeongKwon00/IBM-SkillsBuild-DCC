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

# Agent IDs
CONTEXT_AGENT_ID = os.getenv("CONTEXT_AGENT_ID", "")         # Hesitation detection + phrase pipeline
DIARIZATION_AGENT_ID = os.getenv("DIARIZATION_AGENT_ID", "") # Per-window student/other classifier

# Thresholds
PHRASE_AUTO_DISMISS_S = 5         # Phrase cards auto-dismiss
ORCHESTRATE_TIMEOUT_S = 15.0     # Timeout per Orchestrate call
MIN_SPEECH_CONFIDENCE = 0.6      # Minimum Web Speech API confidence to accept transcript
TRANSCRIPT_WINDOW = 10           # Number of transcript segments to keep in sliding window
SESSION_TIMEOUT_S = 1800         # Evict sessions idle longer than this (30 min)
MPM_WINDOW_SIZE = 8              # Sentences per DiarizationAgent window (paper-optimal)
MPM_TIE_BREAK_LABEL = "student"  # Majority-vote tie-break favors hesitation detection

FALLBACK_PHRASES = [
    "Could you repeat that, please?",
    "Let me think about that for a moment.",
    "I understand. Thank you.",
]
