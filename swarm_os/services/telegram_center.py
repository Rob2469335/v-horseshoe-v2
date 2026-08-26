"""Telegram command center (2026 SOTA — OpenClaw-gateway pattern, self-hosted).

The Command Center's chat presence + phone push in one place. Telegram over
LONG-POLLING (getUpdates) — outbound-only HTTPS, zero inbound ports, no
domain/TLS/port-forward, survives dynamic-IP churn. This is the OpenClaw
consensus transport for a personal home-server bot.

Design (from the "best route" research, cross-verified by two independent
research agents):

  * Thin channel seam, not a framework: inbound messages normalize into one
    envelope; replies route back to Telegram deterministically (the bot never
    picks a channel). The approval_registry is already the pending-action store,
    so chat approvals resolve deterministically in code — never through the LLM.
  * Identity allowlist, fail-closed: only the owner's numeric Telegram user ID
    may command the bot. Every chat message is UNTRUSTED DATA, not instructions
    — enforcement stays at the existing tool boundary (tool_executor /
    approval_registry), the chat adapter just feeds it.
  * Approval-over-chat: when a tool call hits CONFIRM/ALWAYS_CONFIRM, the
    bot sends an inline keyboard with [✓ Approve] [✗ Deny] carrying the opaque
    pending_id; the callback resolves via approval_registry.execute_approved
    (digest-bound, one-time, TTL) and the buttons gray out.

Disabled when TELEGRAM_BOT_TOKEN is absent (zero overhead otherwise). Never
raises out of the daemon: failures are logged and the loop retries.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from typing import Any

import httpx

from ..core.settings import get_settings

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}"
_POLL_TIMEOUT_S = 25  # long-poll (seconds); Telegram allows up to 50
_MAX_MESSAGE_LEN = 4000
_MAX_POLL_BACKOFF_S = 30.0


_pending_pairing = {}
_pairing_lock = None  # lazy-init

_dynamic_owners_cache = None
_dynamic_owners_mtime = 0.0


def _generate_pin() -> str:
    import secrets

    return str(secrets.randbelow(900000) + 100000)


async def _add_owner(user_id: Any) -> None:
    import json
    from pathlib import Path

    global _dynamic_owners_cache

    config_path = Path("swarm_config.json")
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.error("swarm_config.json corrupt: %s", exc)

    owners = config.get("telegram_owners", [])
    if str(user_id) not in owners:
        owners.append(str(user_id))
    config["telegram_owners"] = owners
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    _dynamic_owners_cache = owners


def _cfg() -> dict:
    """Bot config from env/.env. Empty dict = disabled."""
    import json
    from pathlib import Path

    s = get_settings()
    token = os.getenv("TELEGRAM_BOT_TOKEN") or getattr(s, "telegram_bot_token", None)
    owner = os.getenv("TELEGRAM_OWNER_ID") or getattr(s, "telegram_owner_id", None)

    global _dynamic_owners_cache, _dynamic_owners_mtime
    config_path = Path("swarm_config.json")

    try:
        mtime = config_path.stat().st_mtime if config_path.exists() else 0.0
        if _dynamic_owners_cache is None or mtime > _dynamic_owners_mtime:
            if config_path.exists():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                _dynamic_owners_cache = config.get("telegram_owners", [])
            else:
                _dynamic_owners_cache = []
            _dynamic_owners_mtime = mtime
    except Exception:
        if _dynamic_owners_cache is None:
            _dynamic_owners_cache = []

    dynamic_owners = _dynamic_owners_cache

    all_owners = []
    if owner:
        all_owners.extend([o.strip() for o in str(owner).split(",")])
    all_owners.extend([str(o) for o in dynamic_owners])

    if not token:
        return {}
    return {"token": str(token), "owners": all_owners}


def enabled() -> bool:
    return bool(_cfg().get("token"))


def is_owner(user_id: Any) -> bool:
    """Fail-closed identity allowlist: only the owner may command the bot."""
    cfg = _cfg()
    owners = cfg.get("owners", [])
    if not owners:
        return False
    return str(user_id) in owners


class TelegramClient:
    """Minimal Telegram Bot API client (raw httpx — no new dependency)."""

    def __init__(self, token: str) -> None:
        self._base = _API_BASE.format(token=token)
        self._client = httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def _call(self, method: str, payload: dict | None = None) -> dict | None:
        url = f"{self._base}/{method}"
        try:
            resp = await self._client.post(url, json=payload or {})
            data = resp.json()
            if not data.get("ok"):
                log.warning("telegram %s failed: %s", method, data.get("description"))
                return None
            return data.get("result")
        except Exception as exc:
            log.warning("telegram %s error: %s", method, exc)
            return None

    async def get_updates(self, offset: int) -> list[dict] | None:
        result = await self._call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": _POLL_TIMEOUT_S,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        if result is None:
            return None  # API failure (_call logged it) — caller must back off
        return result

    async def send_message(
        self,
        chat_id: Any,
        text: str,
        *,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
    ) -> None:
        text = (text or "")[:_MAX_MESSAGE_LEN]
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        await self._call("sendMessage", payload)

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self._call(
            "answerCallbackQuery", {"callback_query_id": callback_id, "text": text}
        )

    async def edit_message_reply_markup(
        self, chat_id: Any, message_id: int, reply_markup: dict
    ) -> None:
        await self._call(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": json.dumps(reply_markup),
            },
        )


class TelegramCommandCenter:
    """The gateway: long-poll loop, identity gate, command dispatch, and the
    approval bridge into approval_registry."""

    def __init__(self) -> None:
        self._client: TelegramClient | None = None
        self._offset = 0
        self._task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        cfg = _cfg()
        if not cfg.get("token"):
            log.info("telegram disabled (no TELEGRAM_BOT_TOKEN)")
            return
        if self._task and not self._task.done():
            log.warning("telegram already running (suppressing duplicate start)")
            return
        self._client = TelegramClient(cfg["token"])
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run())
        log.info(
            "telegram command center started (owner=%s)", cfg.get("owner", "(unset)")
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()

    # -- the long-poll loop -------------------------------------------------
    async def _run(self) -> None:
        if not self._client:
            return
        backoff = 1.0
        while True:
            try:
                updates = await self._client.get_updates(self._offset)
                if updates is None:
                    # API-level failure (e.g. 502 Bad Gateway): _call already
                    # logged it — back off exponentially instead of re-polling
                    # at round-trip speed and soft-throttling the bot IP.
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_POLL_BACKOFF_S)
                    continue
                backoff = 1.0
                for u in updates:
                    self._offset = max(self._offset, int(u.get("update_id", 0)) + 1)
                    await self._handle_update(u)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("telegram poll error: %s", exc)
                await asyncio.sleep(2.0)

    async def _handle_update(self, update: dict) -> None:
        if not self._client:
            return
        cb = update.get("callback_query")
        if cb:
            await self._handle_callback(cb)
            return
        msg = update.get("message")
        if not msg:
            return
        chat_id = msg.get("chat", {}).get("id")
        text = str(msg.get("text") or "")
        user_id = msg.get("from", {}).get("id")
        if not is_owner(user_id):
            import time

            now = time.time()
            clean_text = str(text).strip()

            global _pairing_lock
            if _pairing_lock is None:
                _pairing_lock = asyncio.Lock()
            async with _pairing_lock:
                expired = [
                    uid for uid, v in _pending_pairing.items() if now - v[1] > 300
                ]
                for uid in expired:
                    del _pending_pairing[uid]

                if user_id in _pending_pairing:
                    pin, ts = _pending_pairing[user_id]
                    if clean_text == pin:
                        del _pending_pairing[user_id]
                        await _add_owner(user_id)
                        await self._client.send_message(
                            chat_id,
                            "✅ Device paired successfully. You are now authorized.",
                        )
                        return
                    else:
                        del _pending_pairing[user_id]

                if user_id not in _pending_pairing:
                    pin = _generate_pin()
                    _pending_pairing[user_id] = (pin, now)
                    log.warning("\n" + "=" * 60)
                    log.warning("PAIRING REQUEST from Telegram user: %s", user_id)
                    log.warning("Reply with PIN: %s to authorize this device.", pin)
                    log.warning("=" * 60 + "\n")

            await self._client.send_message(
                chat_id,
                "⛔ Not authorized. Check server console for pairing PIN and reply with it to authorize this device.",
            )
            return
        if text.startswith("/approve") or text.startswith("/deny"):
            # Manual fallback path: /approve <pending_id> from the owner.
            parts = text.split()
            pending_id = parts[1] if len(parts) > 1 else ""
            await self._resolve_approval(
                chat_id, pending_id, approve=text.startswith("/approve")
            )
            return
        await self._handle_command(chat_id, text)

    # -- command dispatch ----------------------------------------------------
    async def _handle_command(self, chat_id: Any, text: str) -> None:
        cmd = text.strip().split()[0].lower() if text.strip() else "/help"
        if cmd == "/help":
            await self._client.send_message(
                chat_id,
                "Command Center via Telegram.\n\n"
                "/status — what's running\n"
                "/digest — today's news digest\n"
                "/research <goal> — deep research (fan-out + iterative)\n"
                "/approve <pending_id> / /deny <pending_id> — resolve a pending action\n"
                "/inbox — last 5 emails\n"
                '/cron add "goal" "schedule" — add a background task\n'
                '/learn "instruction" — save a permanent skill/rule\n'
                "Anything else is sent to the swarm as a goal.",
            )
        elif cmd == "/status":
            await self._send_status(chat_id)
        elif cmd == "/digest":
            await self._send_news_digest(chat_id)
        elif cmd == "/research":
            await self._send_research(chat_id, text[len(cmd) :].strip())
        elif cmd == "/inbox":
            await self._send_inbox(chat_id)
        elif cmd == "/cron":
            await self._handle_cron_cmd(chat_id, text[len(cmd) :].strip())
        elif cmd == "/learn":
            await self._handle_learn_cmd(chat_id, text[len(cmd) :].strip())
        else:
            # Free-form -> route to the swarm as a goal (bounded, read-mostly).
            await self._client.send_message(
                chat_id,
                f"🧠 Received as a swarm goal: {html.escape(text.strip()[:200])}\n"
                "(goal dispatch runs through the existing ceiling gate; "
                "state-changing actions still ask for your approval here.)",
            )
            await self._dispatch_goal(text.strip())

    async def _handle_learn_cmd(self, chat_id: Any, instruction: str) -> None:
        if not instruction:
            await self._client.send_message(
                chat_id, "Usage: /learn <instruction or rule>"
            )
            return

        from pathlib import Path

        agents_md = Path("AGENTS.md")
        if agents_md.exists():
            content = agents_md.read_text(encoding="utf-8")
            if "## Custom Learned Skills" not in content:
                content += "\n\n## Custom Learned Skills\n"
            content += f"- {instruction}\n"
            agents_md.write_text(content, encoding="utf-8")
            await self._client.send_message(
                chat_id, "✅ Skill learned and saved to AGENTS.md."
            )
        else:
            await self._client.send_message(chat_id, "❌ AGENTS.md not found.")

    async def _handle_cron_cmd(self, chat_id: Any, args_text: str) -> None:
        import shlex

        try:
            parts = shlex.split(args_text)
        except ValueError:
            await self._client.send_message(
                chat_id, 'Syntax error in quotes. Use /cron add "goal" "schedule"'
            )
            return

        if not parts:
            await self._client.send_message(
                chat_id,
                'Usage: /cron list | /cron remove <id> | /cron add "goal" "schedule"',
            )
            return

        action = parts[0].lower()
        from .task_scheduler import list_tasks, create_task, delete_task

        if action == "list":
            tasks = list_tasks()
            if not tasks:
                await self._client.send_message(chat_id, "No scheduled tasks.")
                return
            msg = "<b>Scheduled Tasks:</b>\n"
            for t in tasks:
                msg += f"ID: <code>{t['id']}</code>\nGoal: {html.escape(t['goal'])}\nSchedule: {t['schedule']}\n\n"
            await self._client.send_message(chat_id, msg)
        elif action == "remove":
            if len(parts) < 2:
                await self._client.send_message(
                    chat_id, "Usage: /cron remove <task_id>"
                )
                return
            success = delete_task(parts[1])
            if success:
                await self._client.send_message(
                    chat_id, f"✅ Task {html.escape(parts[1])} removed."
                )
            else:
                await self._client.send_message(
                    chat_id, f"❌ Task {html.escape(parts[1])} not found."
                )
        elif action == "add":
            if len(parts) < 3:
                await self._client.send_message(
                    chat_id, 'Usage: /cron add "goal" "schedule"'
                )
                return
            goal = parts[1]
            schedule = parts[2]
            try:
                task = create_task(goal, schedule)
                await self._client.send_message(
                    chat_id,
                    f"✅ Created task {html.escape(task['id'])}\nGoal: {html.escape(goal)}\nSchedule: {html.escape(schedule)}",
                )
            except Exception as exc:
                log.warning("telegram /cron failed: %s", exc)
                await self._client.send_message(chat_id, "Failed to create task.")
        else:
            await self._client.send_message(
                chat_id,
                'Usage: /cron list | /cron remove <id> | /cron add "goal" "schedule"',
            )

    async def _send_status(self, chat_id: Any) -> None:
        from .task_scheduler import list_tasks

        lines = ["<b>Command Center</b>"]
        try:
            tasks = list_tasks()
            due = [t for t in tasks if t.get("enabled", True)]
            lines.append(f"• {len(due)} scheduled task(s)")
        except Exception as exc:
            log.warning("telegram /status scheduler failed: %s", exc)
            lines.append("• scheduler: unavailable")
        await self._client.send_message(chat_id, "\n".join(lines))

    async def _send_news_digest(self, chat_id: Any) -> None:
        async def _run():
            from .news_digest import digest

            res = await digest(max_items=30)
            text = (
                res.get("digest", "no digest")
                if res.get("ok")
                else "digest unavailable"
            )
            await self._client.send_message(
                chat_id, f"<b>Today's digest</b>\n\n{html.escape(text)}"
            )

        asyncio.create_task(_run())

    async def _send_research(self, chat_id: Any, goal: str) -> None:
        if not goal:
            await self._client.send_message(chat_id, "Usage: /research <goal>")
            return
        await self._client.send_message(
            chat_id, f"🔬 Researching: {html.escape(goal[:200])}…"
        )

        async def _run():
            from .deep_research import deep_research

            try:
                res = await deep_research(goal, max_sub_questions=5, max_iterations=1)
                answer = res.get("answer", "") or "no synthesis"
                cites = "\n".join(
                    f"[{c['n']}] {html.escape(c.get('title', ''))} {c.get('url', '')}"
                    for c in res.get("citations", [])[:10]
                )
                safe_ans = html.escape(answer)
                await self._client.send_message(
                    chat_id, f"{safe_ans}\n\n{cites}" if cites else safe_ans
                )
            except Exception as exc:
                log.warning("telegram research failed: %s", exc)
                await self._client.send_message(chat_id, "Research failed.")

        asyncio.create_task(_run())

    async def _send_inbox(self, chat_id: Any) -> None:
        from .email_service import email_list

        res = await asyncio.to_thread(email_list, "INBOX", 5)
        if not res.get("ok"):
            await self._client.send_message(
                chat_id, f"Email unavailable: {html.escape(res.get('error', '?'))}"
            )
            return
        lines = ["<b>Inbox (last 5)</b>"]
        for m in res.get("messages", []):
            lines.append(f"• {html.escape(m.get('subject', '(no subject)')[:80])}")
        await self._client.send_message(chat_id, "\n".join(lines) or "Empty inbox.")

    async def _dispatch_goal(self, goal: str) -> None:
        """Route a free-form message to the swarm's goal machinery. The ceiling
        gate (task_scheduler.is_scheduler_allowed) bounds it — nothing
        state-changing runs unattended."""

        async def _run():
            try:
                from .task_scheduler import (
                    _ceiling_gate,
                    _goal_is_known_safe,
                    _default_runner,
                )

                allowed, reason = _ceiling_gate(goal)
                if not allowed or not _goal_is_known_safe(goal):
                    log.info("telegram goal refused by ceiling: %s", reason)
                    return
                task = {"goal": goal}
                await _default_runner(task)
            except Exception as exc:
                log.warning("telegram goal dispatch failed: %s", exc)

        asyncio.create_task(_run())

    # -- approval bridge ------------------------------------------------------
    async def _handle_callback(self, cb: dict) -> None:
        if not self._client:
            return
        data = str(cb.get("data") or "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id")
        message_id = cb.get("message", {}).get("message_id")
        user_id = cb.get("from", {}).get("id")
        if not is_owner(user_id):
            await self._client.answer_callback(cb.get("id", ""), "Not authorized.")
            return
        # callback data format: "approve:<pending_id>" | "deny:<pending_id>"
        action, sep, pending_id = data.partition(":")
        if not sep or not pending_id:
            await self._client.answer_callback(cb.get("id", ""), "Bad payload.")
            return
        await self._resolve_approval(
            chat_id,
            pending_id,
            approve=(action == "approve"),
            message_id=message_id,
            callback_id=cb.get("id", ""),
        )

    async def _resolve_approval(
        self,
        chat_id: Any,
        pending_id: str,
        *,
        approve: bool,
        message_id: int | None = None,
        callback_id: str | None = None,
    ) -> None:
        from .approval_registry import get_registry

        registry = get_registry()
        rec = registry.peek(pending_id)
        if rec is None:
            note = "That pending action is unknown or expired."
        else:
            tool = rec.get("tool", "?")
            action = rec.get("action") or ""
            if approve:
                consumed = registry.consume_any(pending_id)
                if consumed:
                    note = f"✅ Approved {html.escape(tool)} {html.escape(action)}"
                    await self._dispatch_approved(consumed)
                else:
                    note = "That action was already consumed or expired."
            else:
                registry.deny(pending_id)
                note = f"⛔ Denied {html.escape(tool)} {html.escape(action)}"
        if self._client:
            if callback_id:
                await self._client.answer_callback(callback_id, note)
            if message_id is not None:
                await self._client.edit_message_reply_markup(chat_id, message_id, {})
            else:
                await self._client.send_message(chat_id, note)

    async def _dispatch_approved(self, rec: dict) -> None:
        """Execute an already-consumed approved pending action.

        Uses tool_executor._dispatch directly (NOT run()) — run() re-applies the
        approval gate, and with auth=None a CONFIRM/ALWAYS_CONFIRM action would
        silently create a NEW pending action and return confirmation_required
        instead of executing the one the owner just approved. The record was
        atomically consumed via consume_any() (which verifies the stored
        payload), so direct dispatch of the stored payload is the correct seam.
        """

        async def _run():
            try:
                from runtime_v2.services.tool_executor import _dispatch

                tool = rec.get("tool")
                payload = rec.get("payload") or {}
                result = await _dispatch(tool, payload)
                log.info(
                    "telegram-approved action executed: tool=%s ok=%s",
                    tool,
                    result.get("ok"),
                )
            except Exception as exc:
                log.warning("telegram-approved action execution failed: %s", exc)

        asyncio.create_task(_run())


# ---------------------------------------------------------------------------
# Module-level singleton + notify API (used by other services to push)
# ---------------------------------------------------------------------------
_center: TelegramCommandCenter | None = None


def get_center() -> TelegramCommandCenter:
    global _center
    if _center is None:
        _center = TelegramCommandCenter()
    return _center


async def notify(text: str, *, parse_mode: str = "HTML") -> bool:
    """Fire the text to the owner via Telegram. No-op when disabled. Used by
    the task scheduler / deep research / news digest for notify-when-done."""
    center = get_center()
    client = center._client
    if not client or not enabled():
        return False
    cfg = _cfg()
    owners = cfg.get("owners", [])
    if not owners:
        return False
    try:
        await client.send_message(owners[0], text, parse_mode=parse_mode)
        return True
    except Exception as exc:
        log.warning("telegram notify failed: %s", exc)
        return False
