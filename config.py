"""Model settings from the environment. No secrets in code.""" 

import os
from pathlib import Path 

ROOT = Path(__file__).resolve().parent 

def load_dotenv(path=None):
    """Read KEY=VALUE lines from .env into os.environ (file wins)."""
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.exists():
        return
    with env_path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key:
                os.environ[key] = value 

load_dotenv()

# Copy .env.example to .env and put your key there.

PROVIDER = os.environ.get("MODEL_PROVIDER", "gemini")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
API_KEY = os.environ.get("GEMINI_API_KEY", "")
SLEEP_SECONDS = float(os.environ.get("GEMINI_SLEEP_SECONDS", "4"))
MAX_CALLS = int(os.environ.get("GEMINI_MAX_CALLS", "0"))  # 0 = no cap
RETRIES = int(os.environ.get("GEMINI_RETRIES", "0"))  # extra attempts per message (0 = no loop)
RETRY_WAIT = float(os.environ.get("GEMINI_RETRY_WAIT", "5"))
# Give up on the model after this many consecutive failures; the run continues.
MAX_FAILURES = int(os.environ.get("GEMINI_MAX_FAILURES", "2"))
# Wall-clock budget for Gemini in one demo run. 0 = no time cap.
MAX_SECONDS = float(os.environ.get("GEMINI_MAX_SECONDS", "45"))

 

def ready():
    """True when a real-looking key is present."""
    key = (API_KEY or "").strip()
    if not key or key == "your_key_here":
        return False
    return True