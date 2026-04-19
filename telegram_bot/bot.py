"""
bot.py — Telegram Application wiring and message handler orchestration.

All security, execution, context, and reflection logic lives in their
respective modules. This module is the glue.
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import config, adapter, security

log = logging.getLogger("bot")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EDIT_THROTTLE_SECS = 2.0    # minimum seconds between Telegram message edits
CONVERSATIONAL_MAX_LEN = 15  # messages <= this many chars with no task verb → chat response

# Secrets list for credential scrubbing (populated at startup)
_SECRETS: list[str] = []

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def build_application() -> Application:
    """Build and return the configured Telegram Application."""
    global _SECRETS
    _SECRETS = [
        s for s in [config.TELEGRAM_BOT_TOKEN, config.ANTHROPIC_API_KEY]
        if s
    ]

    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )

    app.bot_data["task_lock"] = asyncio.Lock()
    app.bot_data["running_task"] = None   # dict with task_id, description, started_at, proc_pid

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("help", _cmd_help))
    app.add_handler(CommandHandler("status", _cmd_status))
    app.add_handler(CommandHandler("cancel", _cmd_cancel))
    app.add_handler(CommandHandler("clear", _cmd_clear))

    # Per-project shorthand commands
    _PROJECT_COMMANDS = {
        # talon
        "homelabber":   "homelabber",
        "discord":      "discord-bot",
        "exec":         "exec-assistant",
        # homeclaw
        "infra":        "infra",
        "clarvis":      "clarvis-ai",
        "personal":     "personal-assistant",
        "procurement":  "home-procurement",
        "homeclaw":     "homeclaw",
    }
    for cmd, project_key in _PROJECT_COMMANDS.items():
        app.add_handler(CommandHandler(cmd, _make_project_cmd_handler(project_key)))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    app.post_init = _set_commands

    return app


async def _set_commands(app) -> None:
    """Register visible command list with Telegram (shows in autocomplete)."""
    from telegram import BotCommand
    commands = [
        BotCommand("start",       "Start the bot"),
        BotCommand("help",        "Show help"),
        BotCommand("status",      "Show current task"),
        BotCommand("cancel",      "Cancel running task"),
        BotCommand("clear",       "Wipe task history"),
        # talon
        BotCommand("homelabber",  "[@persona] task — run in homelabber project"),
        BotCommand("discord",     "[@persona] task — run in discord-bot project"),
        BotCommand("exec",        "[@persona] task — run in exec-assistant project"),
        # homeclaw
        BotCommand("infra",       "[@persona] task — run in infra project"),
        BotCommand("clarvis",     "[@persona] task — run in clarvis-ai project"),
        BotCommand("personal",    "[@persona] task — run in personal-assistant project"),
        BotCommand("procurement", "[@persona] task — run in home-procurement project"),
        BotCommand("homeclaw",    "[@persona] task — run at homeclaw root"),
    ]
    await app.bot.set_my_commands(commands)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _make_project_cmd_handler(project_key: str):
    """
    Factory: returns an async CommandHandler callback for a project shorthand command.

    Usage:  /infra [@persona] task text
    - If first arg starts with @, it's an explicit persona (e.g. @dev, @planner).
    - Remaining args are the task text.
    - No @ prefix: all args are the task, default AGENT.md is used.
    - No args: reply with usage hint.
    """
    async def _handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not security.is_allowed_user(update.effective_chat.id):
            return

        args = ctx.args or []
        if not args:
            cmd = update.message.text.split()[0].lstrip("/")
            await update.message.reply_text(
                f"Usage: /{cmd} [@persona] task text\n"
                f"Example: /{cmd} @planner design the VPN layout\n"
                f"         /{cmd} do the thing  (uses default AGENT.md)"
            )
            return

        # Explicit @persona prefix — unambiguous, no auto-detection
        persona: str | None = None
        task_parts: list[str] = args

        if args[0].startswith("@"):
            persona = args[0][1:] or None  # strip @; bare "@" treated as no persona
            task_parts = args[1:]
            try:
                project_path, persona_file = adapter.resolve_route(project_key, persona)
            except ValueError as e:
                await update.message.reply_text(f"Routing error: {e}")
                return
        else:
            try:
                project_path, persona_file = adapter.resolve_route(project_key, None)
            except ValueError as e:
                await update.message.reply_text(f"Routing error: {e}")
                return

        task_text = " ".join(task_parts).strip()
        if not task_text:
            cmd = update.message.text.split()[0].lstrip("/")
            persona_hint = f"@{persona}" if persona else "@planner"
            await update.message.reply_text(
                f"Provide a task after the persona. Example: /{cmd} {persona_hint} do the thing"
            )
            return

        hit, kw = security.contains_forbidden_keyword(task_text)
        if hit:
            await update.message.reply_text(f"Refused: security policy ('{kw}').")
            return

        lock: asyncio.Lock = ctx.bot_data["task_lock"]
        if lock.locked():
            await update.message.reply_text("Still working on the previous task. Send /cancel to abort it.")
            return

        quoted_context = ""
        if update.message.reply_to_message:
            quoted_text = (update.message.reply_to_message.text or "").strip()
            if quoted_text:
                quoted_context = (
                    "[Context: user is following up on this previous bot response]\n"
                    f"{quoted_text[:2000]}\n"
                    "[End context]\n"
                )

        async with lock:
            await _run_task(update, ctx, task_text, quoted_context, project_path, persona_file)

    return _handler


async def _cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not security.is_allowed_user(update.effective_chat.id):
        return
    await update.message.reply_text(
        "Homelabber ready. Send me a task and I'll run it on your machine."
    )


async def _cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not security.is_allowed_user(update.effective_chat.id):
        return
    await update.message.reply_text(
        "/status — show current task\n"
        "/cancel — abort the running task\n"
        "/clear  — wipe task history (use if bot is stuck in a loop)\n"
        "/help   — this message\n\n"
        "Just send a message to run a task."
    )


async def _cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not security.is_allowed_user(update.effective_chat.id):
        return
    running = ctx.bot_data.get("running_task")
    if not running:
        await update.message.reply_text("Idle — no task running.")
        return
    elapsed = int(time.time() - running["started_at"])
    await update.message.reply_text(
        f"Running ({elapsed}s): {running['description'][:100]}"
    )


async def _cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Wipe task history — use when the bot is stuck in a failure loop."""
    if not security.is_allowed_user(update.effective_chat.id):
        return
    cleared = adapter.clear_task_history()
    await update.message.reply_text(f"Cleared {cleared} task(s) from history. Bot is reset.")


async def _cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not security.is_allowed_user(update.effective_chat.id):
        return
    running = ctx.bot_data.get("running_task")
    if not running:
        await update.message.reply_text("Nothing running to cancel.")
        return
    pid = running.get("proc_pid")
    if pid:
        try:
            import os, signal
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    await update.message.reply_text("Cancelled.")


# ---------------------------------------------------------------------------
# Main message handler
# ---------------------------------------------------------------------------

async def _handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    # --- Gate 1: whitelist ---
    if not security.is_allowed_user(chat_id):
        return  # silent drop

    # --- Gate 2: conversational shortcut (no Claude invocation) ---
    if _is_conversational(text):
        await update.message.reply_text("Send me a task and I'll get to work.")
        return

    # --- Parse optional [project/persona] prefix ---
    project_path: str = config.ALLOWED_PROJECT_ROOT + "/homelabber"
    task_text: str = text
    persona_file: str | None = None

    parsed = adapter.parse_prefix(text)
    if parsed is not None:
        proj, persona, task_text = parsed
        try:
            project_path, persona_file = adapter.resolve_route(proj, persona)
        except ValueError as e:
            await update.message.reply_text(f"Routing error: {e}")
            return

    # --- Gate 3: forbidden keyword filter (applied to task text, not prefix) ---
    hit, kw = security.contains_forbidden_keyword(task_text)
    if hit:
        await update.message.reply_text(f"Refused: security policy ('{kw}').")
        return

    # --- Gate 4: concurrency lock ---
    lock: asyncio.Lock = ctx.bot_data["task_lock"]
    if lock.locked():
        await update.message.reply_text(
            "Still working on the previous task. Send /cancel to abort it."
        )
        return

    # Extract reply-to context for contextual debugging
    quoted_context = ""
    if update.message.reply_to_message:
        quoted_text = (update.message.reply_to_message.text or "").strip()
        if quoted_text:
            quoted_context = (
                "[Context: user is following up on this previous bot response]\n"
                f"{quoted_text[:2000]}\n"
                "[End context]\n"
            )

    async with lock:
        await _run_task(update, ctx, task_text, quoted_context, project_path, persona_file)


async def _run_task(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    text: str,
    quoted_context: str = "",
    project_path: str | None = None,
    persona_file: str | None = None,
) -> None:
    """Execute a task: validate path, spawn Claude, stream result, reflect."""
    if project_path is None:
        project_path = config.ALLOWED_PROJECT_ROOT + "/homelabber"

    # Validate path (defence-in-depth)
    try:
        project_path = security.validate_project_path(project_path)
    except ValueError as e:
        await update.message.reply_text(f"Error: {e}")
        return

    # Record task in history
    task_id = adapter.record_task(text)

    # Build the prompt: inject persona read instruction if applicable, then rolling context
    effective_task = adapter.build_persona_prompt(text, persona_file)
    context_block = adapter.format_context_block()
    full_prompt = effective_task
    if quoted_context or context_block:
        parts = []
        if quoted_context:
            parts.append(quoted_context)
        if context_block:
            parts.append(context_block)
        parts.append("[Current task]\n" + effective_task)
        full_prompt = "\n\n".join(parts)

    # Send "working..." message for live updates
    working_msg = await update.message.reply_text("Working...")
    working_msg_id = working_msg.message_id

    # Set up live update state
    _live_state = {
        "last_edit": 0.0,
        "accumulated": "",
    }

    async def on_chunk(chunk: str) -> None:
        _live_state["accumulated"] += chunk
        now = time.time()
        if now - _live_state["last_edit"] >= EDIT_THROTTLE_SECS:
            _live_state["last_edit"] = now
            preview = _live_state["accumulated"][-config.MAX_OUTPUT_CHARS:]
            try:
                await ctx.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=working_msg_id,
                    text=f"Working...\n\n{preview}",
                )
            except Exception:
                pass  # edit failures are non-fatal

    # Track running task for /status and /cancel
    ctx.bot_data["running_task"] = {
        "task_id": task_id,
        "description": text[:100],
        "started_at": time.time(),
        "proc_pid": None,  # executor sets this if we expose it — future enhancement
    }

    try:
        result = await adapter.run_claude(
            prompt=full_prompt,
            project_path=project_path,
            task_id=task_id,
            on_chunk=on_chunk,
        )
    except Exception as e:
        log.exception("Unexpected error in run_claude")
        ctx.bot_data["running_task"] = None
        adapter.update_task(task_id, "error", str(e)[:200])
        await ctx.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=working_msg_id,
            text=f"Internal error: {e}",
        )
        return
    finally:
        ctx.bot_data["running_task"] = None

    # --- Format and send result ---
    if result.timed_out:
        output = f"Task timed out after {config.CLAUDE_TIMEOUT}s."
        status = "timeout"
        log.warning(f"task_id={task_id} timed out")
    elif result.returncode != 0 and not result.stdout:
        if result.returncode == 143:
            err = "process killed by SIGTERM — bot may have restarted mid-task, or /cancel was sent"
        else:
            err = result.stderr[:500] if result.stderr else "unknown error"
        output = f"Task failed (exit {result.returncode}):\n{err}"
        status = "error"
        log.error(f"task_id={task_id} exit={result.returncode} stderr={result.stderr[:200]}")
    else:
        output = result.stdout or "(no output)"
        status = "success" if result.returncode == 0 else "error"
        log.info(f"task_id={task_id} status={status} stdout_len={len(result.stdout)}")

    # Scrub credentials before sending to Telegram
    output = security.scrub_credentials(output, _SECRETS)

    # Prepend tool call summary (execution transparency)
    tool_summary = _format_tool_calls(result.tool_calls)
    if tool_summary:
        output = f"{tool_summary}\n\n{output}"

    # Update task history
    adapter.update_task(task_id, status, output[:8000])

    # Send final output (replacing or supplementing the "Working..." message)
    await _send_output(ctx, update.effective_chat.id, working_msg_id, output)

    # Reflection pass disabled — direct API calls require API credits, not available on subscription
    # asyncio.create_task(reflect.reflect(...))


async def _send_output(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    working_msg_id: int,
    output: str,
) -> None:
    """
    Edit the "Working..." message with final output.
    URLs in the output are extracted and rendered as inline keyboard buttons.
    If output exceeds Telegram's limit, split into multiple messages.
    """
    limit = config.TELEGRAM_MSG_LIMIT

    # Extract URLs → buttons (applied to the final chunk only)
    body, keyboard = _extract_url_keyboard(output)

    if len(body) <= limit:
        try:
            await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=working_msg_id,
                text=body or "(no output)",
                reply_markup=keyboard,
            )
        except Exception:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=body or "(no output)",
                reply_markup=keyboard,
            )
        return

    # Split body into chunks of at most `limit` chars
    chunks = [body[i:i+limit] for i in range(0, len(body), limit)]
    max_chunks = 3

    # Edit the working message with first chunk (no buttons yet)
    try:
        await ctx.bot.edit_message_text(
            chat_id=chat_id,
            message_id=working_msg_id,
            text=chunks[0],
        )
    except Exception:
        await ctx.bot.send_message(chat_id=chat_id, text=chunks[0])

    # Send remaining chunks; attach buttons to the last one
    for i, chunk in enumerate(chunks[1:max_chunks], start=1):
        is_last = i == min(len(chunks) - 1, max_chunks - 1)
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=chunk,
            reply_markup=keyboard if is_last else None,
        )

    if len(chunks) > max_chunks:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=f"[output truncated — {len(body)} chars total]",
            reply_markup=keyboard,
        )


def _format_tool_calls(tool_calls: list[dict]) -> str:
    """
    Format tool calls as a compact 'Actions' block for Telegram.
    Skips Glob/Grep (high-volume, low signal). Caps at 8 displayed.
    """
    SKIP_TOOLS = {"Glob", "Grep"}
    MAX_DISPLAY = 8
    filtered = [tc for tc in tool_calls if tc.get("name") not in SKIP_TOOLS]
    if not filtered:
        return ""
    lines = ["📋 Actions:"]
    for tc in filtered[:MAX_DISPLAY]:
        name = tc.get("name", "?")
        inp = tc.get("input", {})
        first_val = next(iter(inp.values()), "") if inp else ""
        if first_val:
            lines.append(f"• {name}: {str(first_val)[:80]}")
        else:
            lines.append(f"• {name}")
    if len(filtered) > MAX_DISPLAY:
        lines.append(f"  ... and {len(filtered) - MAX_DISPLAY} more")
    return "\n".join(lines)


_URL_RE = re.compile(r"https?://\S+")


def _extract_url_keyboard(text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """
    Pull URLs out of text and return (cleaned_text, InlineKeyboardMarkup | None).
    Each URL becomes a button labelled with its domain.  If no URLs found, returns
    the text unchanged and None.
    """
    urls = list(dict.fromkeys(_URL_RE.findall(text)))  # dedupe, preserve order
    if not urls:
        return text, None

    # Strip trailing punctuation that crept into the match (brackets, periods, etc.)
    clean_urls = []
    for u in urls:
        u = u.rstrip(".,;:!?)\"'>]")
        clean_urls.append(u)

    # Remove raw URLs from the text body — button replaces them
    cleaned = text
    for u in clean_urls:
        cleaned = cleaned.replace(u, "").strip()

    buttons = []
    for u in clean_urls:
        try:
            domain = re.sub(r"^https?://", "", u).split("/")[0]
            label = f"Open {domain}"
        except Exception:
            label = "Open link"
        buttons.append([InlineKeyboardButton(text=label, url=u)])

    return cleaned, InlineKeyboardMarkup(buttons)


def _is_conversational(text: str) -> bool:
    """
    Return True for very short messages that are clearly not tasks.
    Avoids spinning up Claude Code for "hi", "thanks", "ok", etc.
    """
    if len(text) > CONVERSATIONAL_MAX_LEN:
        return False
    conversational = {"hi", "hey", "hello", "thanks", "thank you", "ok", "okay",
                      "great", "cool", "good", "bye", "yes", "no", "yep", "nope"}
    return text.lower() in conversational
