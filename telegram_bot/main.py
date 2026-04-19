"""
main.py — Entry point for the Homelabber Telegram bot.

Single-instance enforcement via PID file.
Signal handling for clean shutdown.

Run: python3 -m telegram_bot.main  (from telegram-bot/ directory)
"""
import argparse
import logging
import os
import sys
from pathlib import Path

from . import config, adapter
from .bot import build_application

# ---------------------------------------------------------------------------
# PID file
# ---------------------------------------------------------------------------
PID_FILE = Path.home() / ".homelabber-bot.pid"


def _check_pid_file() -> None:
    """Abort if another instance is already running. Overwrite stale PID files."""
    if not PID_FILE.exists():
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # raises ProcessLookupError if not alive
        sys.exit(f"[main] ERROR: Bot already running (PID {pid}). Kill it first or delete {PID_FILE}.")
    except ProcessLookupError:
        print(f"[main] Stale PID file found (PID {pid} not running). Overwriting.", flush=True)
    except (ValueError, OSError):
        pass


def _write_pid_file() -> None:
    PID_FILE.write_text(str(os.getpid()))


def _remove_pid_file() -> None:
    PID_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=level,
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Homelabber Telegram Bot")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    _setup_logging(args.debug)
    log = logging.getLogger("main")

    _check_pid_file()
    _write_pid_file()

    cleaned = adapter.startup_cleanup()
    if cleaned:
        log.info(f"Marked {cleaned} stale task(s) as failed at startup")

    log.info(f"Starting Homelabber bot (allowed_chat_id={config.ALLOWED_CHAT_ID})")
    log.info(f"Project root: {config.ALLOWED_PROJECT_ROOT}")
    log.info(f"Claude CLI: {config.CLAUDE_CLI_PATH}")

    app = build_application()

    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        _remove_pid_file()
        log.info("Bot stopped.")


if __name__ == "__main__":
    main()
