"""
adapter.py — Bridge to homelabber engine modules.

Adds homelabber to sys.path so engine modules (executor, router, context,
security, reflect) can be imported without duplicating their code.

All engine functionality is re-exported from here. Thin wrapper modules
(executor.py, context.py, etc.) in this package then re-export from here
so that bot.py's imports remain unchanged.
"""
import sys

from . import config

# Inject homelabber engine onto the path
if config.HOMELABBER_PATH not in sys.path:
    sys.path.insert(0, config.HOMELABBER_PATH)

# executor
from bot.executor import run_claude, ExecutionResult  # noqa: F401

# router
from bot.router import (  # noqa: F401
    parse_prefix, resolve_route, build_persona_prompt,
    get_allowed_routes, get_personas,
)

# context
from bot.context import (  # noqa: F401
    record_task, update_task, format_context_block,
    startup_cleanup, get_recent_tasks, clear_task_history,
)

# security (engine-level — no Telegram-specific checks)
from bot.security import (  # noqa: F401
    contains_forbidden_keyword, validate_project_path,
    scrub_credentials, get_system_prompt, is_safe_learning,
)

# reflect
from bot.reflect import reflect  # noqa: F401
