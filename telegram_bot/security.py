"""
security.py — Telegram-specific security checks.

Generic checks (keyword filter, path validation, credential scrubbing) are
re-exported from the homelabber engine. The Telegram-specific user allowlist
check lives here because it depends on ALLOWED_CHAT_ID from this bot's config.
"""
from .adapter import (  # noqa: F401
    contains_forbidden_keyword, validate_project_path,
    scrub_credentials, get_system_prompt, is_safe_learning,
)
from . import config


def is_allowed_user(chat_id: int) -> bool:
    """Return True only if chat_id matches the configured allowed user."""
    return chat_id == config.ALLOWED_CHAT_ID
