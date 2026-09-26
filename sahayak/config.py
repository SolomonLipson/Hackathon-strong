"""
Centralized configuration for Sahayak, the offline triage agent.

Every tunable value lives here so nothing is hard-coded across feature files:
  * which Gemma 4 models to use (primary + smaller fallback) and how to reach
    the local Ollama runtime,
  * agent loop limits (max steps, confidence threshold for human handoff),
  * sync / connectivity behaviour (retry back-off for the outbox),
  * file-system locations for the SQLite store and logs.

Values can be overridden with environment variables so the same code runs on
a laptop, a clinic mini-PC or inside a container without edits.
"""

import os
from pathlib import Path

# --- Paths -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SAHAYAK_DATA_DIR", BASE_DIR.parent / "data"))
DB_PATH = DATA_DIR / "sahayak.db"
LOG_PATH = DATA_DIR / "agent.log"
STATIC_DIR = BASE_DIR / "static"

# --- Local model runtime (Ollama) --------------------------------------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
# Primary: Gemma 4 E4B (QAT, quantization-aware trained for on-device). Fallback: E2B QAT (smaller, survives low RAM).
PRIMARY_MODEL = os.environ.get("SAHAYAK_MODEL", "gemma4:e4b-it-qat")
FALLBACK_MODEL = os.environ.get("SAHAYAK_FALLBACK_MODEL", "gemma4:e2b-it-qat")
LLM_TIMEOUT_S = float(os.environ.get("SAHAYAK_LLM_TIMEOUT", "120"))
LLM_RETRIES = 1  # extra attempts per model before moving down the fallback chain
LLM_TEMPERATURE = 0.1
LLM_NUM_CTX = 4096  # one fixed context size for every call, so Ollama never reloads the model

LLM_KEEP_ALIVE = "30m"     # keep Gemma resident in memory between calls
LLM_MAX_TOKENS = 700       # cap on generated tokens per call

# --- Read-aloud (offline system text-to-speech) -------------------------------
# macOS voices via `say`; on Linux espeak-ng is used if installed.
TTS_VOICES = {"Hindi": "Lekha", "Telugu": "Geeta", "English": "Rishi"}

# --- Agent loop --------------------------------------------------------------
MAX_STEPS = 12              # hard ceiling on decide/act/check iterations
MAX_CHECK_REJECTIONS = 3    # checker rejections before forcing human handoff
HANDOFF_CONFIDENCE = 0.55   # below this the agent must hand off to a clinician

# --- Sync outbox (spotty connectivity) ---------------------------------------
SYNC_BASE_BACKOFF_S = 5
SYNC_MAX_BACKOFF_S = 300

# --- Web server --------------------------------------------------------------
HOST = os.environ.get("SAHAYAK_HOST", "127.0.0.1")
PORT = int(os.environ.get("SAHAYAK_PORT", "8765"))
