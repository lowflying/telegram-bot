"""
config.py — Telegram-specific configuration.

Engine configuration (CLAUDE_TIMEOUT, STATE_DB_PATH, etc.) lives in
homelabber/bot/config.py and is read by the engine modules directly.
This file covers what the Telegram frontend needs on top of that.
"""
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# .env loader (same pattern as homelabber/bot/config.py)
# ---------------------------------------------------------------------------
_ENV_PATH = Path(__file__).parent.parent / ".env"


def _load_env() -> dict[str, str]:
    if not _ENV_PATH.exists():
        return {}
    result = {}
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


_env = _load_env()


def _get(key: str) -> str:
    import os as _os
    return _os.environ.get(key) or _env.get(key, "")


def _require(key: str) -> str:
    val = _get(key)
    if not val:
        sys.exit(f"[config] ERROR: {key} is not set in {_ENV_PATH}. Aborting.")
    return val


# ---------------------------------------------------------------------------
# Required secrets
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")

# Optional — used for reflection pass and credential scrubbing
ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY")

# Optional — separate token for E2E tests
TELEGRAM_TEST_BOT_TOKEN: str = _get("TELEGRAM_TEST_BOT_TOKEN")

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
ALLOWED_CHAT_ID: int = int(_get("ALLOWED_CHAT_ID") or "6237832337")

# ---------------------------------------------------------------------------
# Engine location
# ---------------------------------------------------------------------------
HOMELABBER_PATH: str = _get("HOMELABBER_PATH") or "/home/lowflying/talon/homelabber"

# ---------------------------------------------------------------------------
# Paths (mirrored from engine config for logging; engine reads these itself)
# ---------------------------------------------------------------------------
ALLOWED_PROJECT_ROOT: str = _get("ALLOWED_PROJECT_ROOT") or "/home/lowflying/talon"

# ---------------------------------------------------------------------------
# Execution (mirrored for display in status messages; engine reads its own copy)
# ---------------------------------------------------------------------------
CLAUDE_TIMEOUT: int = int(_get("CLAUDE_TIMEOUT") or "600")

# ---------------------------------------------------------------------------
# Telegram output limits
# ---------------------------------------------------------------------------
MAX_OUTPUT_CHARS: int = 3800       # preview truncation during streaming
TELEGRAM_MSG_LIMIT: int = 4096     # hard Telegram cap

# ---------------------------------------------------------------------------
# Claude CLI (for startup log)
# ---------------------------------------------------------------------------
CLAUDE_CLI_PATH: str = shutil.which("claude") or ""
if not CLAUDE_CLI_PATH:
    sys.exit("[config] ERROR: 'claude' CLI not found on PATH. Install: npm install -g @anthropic-ai/claude-code")
