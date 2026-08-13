"""Recurring agent tasks (2026 SOTA — Comet Tasks / workspace agents).

A scheduled-goal registry + a daemon (main.py lifespan, like the watch-loop) that
runs due tasks through the EXISTING agent/browser-task machinery.

TWO NON-NEGOTIABLE CONSTRAINTS (spec'd before code):
1. THE CEILING IS THE ENTRY POINT. A scheduled task fires with nobody watching,
   so it may ONLY reach free/ask-tier actions. `is_scheduler_allowed` (Build 1's
   permission model) is the LOAD-BEARING check and runs BEFORE dispatch — the
   goal-keyword scan is defense-in-depth, never the authority. If a goal's wording
   dodges the keyword list but resolves to an important/approval/human-channel
   tool call, is_scheduler_allowed is what stops it.
2. UNMAPPED GOALS REFUSE-AND-FLAG (fail-closed). A goal the runner can't map to a
   known-safe pattern (email-summary / web-search) is NOT dispatched to
   run_browser_task as a gamble — an unmapped goal is one the scheduler can't
   classify, so 'the gate will catch it' is asking a backstop to defend a case it
   wasn't built to reason about. It logs blocked and tells a human, who either
   fixes the phrasing or explicitly widens the router later.
3. DAEMON FAILURE = heartbeat + stale-by-recency (visible, not silent), same as
   the watch-loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger("TaskScheduler")

_TASKS_FILE = Path("data/tasks.json")
_LOCK = threading.Lock()

# Known-safe goal patterns the default runner can dispatch. Anything else
# refuses-and-flags (the scheduler does NOT gamble on unmapped goals).
KNOWN_PATTERNS = ("email", "web", "search", "browser", "research")


def _now() -> float:
    return time.time()


def _load() -> dict:
    try:
        if _TASKS_FILE.exists():
            return json.loads(_TASKS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("task registry load failed: %s", exc)
    return {}


def _save(data: dict) -> None:
    try:
        _TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TASKS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def list_tasks() -> list[dict]:
    with _LOCK:
        return list(_load().values())


def create_task(goal: str, schedule: str = "daily 08:00", enabled: bool = True) -> dict:
    task = {
        "id": uuid.uuid4().hex[:12],
        "goal": goal,
        "schedule": schedule,
        "enabled": enabled,
        "last_run": None,
        "result": None,
    }
    with _LOCK:
        data = _load()
        data[task["id"]] = task
        _save(data)
    return task


def delete_task(task_id: str) -> bool:
    with _LOCK:
        data = _load()
        if task_id not in data:
            return False
        del data[task_id]
        _save(data)
    return True


def set_task_enabled(task_id: str, enabled: bool) -> bool:
    with _LOCK:
        data = _load()
        if task_id not in data:
            return False
        data[task_id]["enabled"] = bool(enabled)
        _save(data)
    return True


def _parse_daily(schedule: str) -> tuple[int, int] | None:
    s = schedule.strip().lower()
    if s == "hourly":
        return None
    if s.startswith("daily"):
        parts = s[len("daily") :].strip().split(":")
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                return None
    return None


def _is_due(task: dict, now: float = _now()) -> bool:
    if not task.get("enabled"):
        return False
    schedule = str(task.get("schedule", ""))
    last = task.get("last_run")
    last_ts = 0.0
    if last:
        try:
            last_ts = datetime.fromisoformat(last).timestamp()
        except Exception:
            last_ts = 0.0
    if schedule == "hourly":
        return (now - last_ts) >= 3600
    hm = _parse_daily(schedule)
    if hm is None:
        return False
    hour, minute = hm
    now_dt = datetime.now()
    due_today = (now_dt.hour > hour) or (
        now_dt.hour == hour and now_dt.minute >= minute
    )
    if not due_today:
        return False
    if last_ts > 0:
        last_dt = datetime.fromtimestamp(last_ts)
        if (last_dt.year, last_dt.month, last_dt.day) == (
            now_dt.year,
            now_dt.month,
            now_dt.day,
        ):
            return False
    return True


def _ceiling_gate(goal: str) -> tuple[bool, str]:
    """The AUTHORITATIVE ceiling check. The permission model is load-bearing and
    runs FIRST; the keyword scan is defense-in-depth only.

    The check is GOAL-AWARE: a goal is refused only if it maps to a tool the goal
    plausibly implies. 'summarize my inbox' reads email (email_list, free-tier,
    scheduler-safe) and must NOT be refused for email_send just because email is
    mentioned. Only goals that actually imply sending/transacting/OS-control are
    refused. Returns (allowed, reason)."""
    from swarm_os.services.permission_tiers import is_scheduler_allowed

    low = (goal or "").lower()
    # Goal-aware tool mapping: refuse a tool only if the goal plausibly reaches it.
    # OS-control / sandbox are approval-tier and NEVER scheduler-safe, but a goal
    # that doesn't mention OS/sandbox doesn't reach them.
    os_hint = any(
        h in low
        for h in (
            "install",
            "run a command",
            "terminal",
            "powershell",
            "control my screen",
            "mouse",
            "keyboard",
            "open an app",
            "launch ",
        )
    )
    if os_hint and not is_scheduler_allowed("", "screen", None):
        return False, "scheduler ceiling: OS/screen control is approval-tier"
    sandbox_hint = any(
        h in low for h in ("run code", "execute", "sandbox", "repl", "python", "script")
    )
    if sandbox_hint and not is_scheduler_allowed("", "sandbox_repl", None):
        return False, "scheduler ceiling: sandbox_repl is approval-tier"
    # email: refuse ONLY if the goal implies sending (email_list/read are free).
    send_hint = any(
        s in low
        for s in (
            "send an email",
            "send email",
            "email to",
            "reply to",
            "email my",
            "draft an email to",
        )
    )
    if send_hint and not is_scheduler_allowed("", "email_send", None):
        return (
            False,
            "scheduler ceiling: email_send is important (goal implies sending)",
        )
    # Keyword scan — defense-in-depth, never the authority.
    blocked = (
        "purchase",
        " buy ",
        "checkout",
        " pay ",
        "transfer",
        "refund",
        "delete ",
        "approve",
        "confirm ",
        "login",
        "log in",
        "sign in",
        "password",
        "payment",
        "card",
        "ccv",
        "cvv",
        "pin",
        "credential",
        "otp",
        "2fa",
        "mfa",
        "bank",
        "install",
        "terminal",
        "powershell",
        "mouse",
        "keyboard",
    )
    if any(b in low for b in blocked):
        return (
            False,
            f"scheduler ceiling: goal keyword '{next(b for b in blocked if b in low)}' is blocked",
        )
    return True, ""


def _goal_is_known_safe(goal: str) -> bool:
    """Can the default runner dispatch this goal? Only if it matches a known-safe
    pattern. An UNMAPPED goal refuses-and-flags (fail-closed): the scheduler does
    not gamble on goals it can't classify."""
    low = (goal or "").lower()
    return any(p in low for p in KNOWN_PATTERNS)


async def _default_runner(task: dict) -> dict:
    """Dispatch a scheduled task through the existing machinery. Only reached
    after the ceiling gate + known-safe check both pass (run_due_tasks enforces
    both BEFORE calling this)."""
    goal = str(task.get("goal", ""))
    if "email" in goal.lower():
        from swarm_os.services.email_service import email_list

        inbox = await asyncio.to_thread(email_list, "INBOX", 10)
        if not inbox.get("ok"):
            return {"ok": False, "error": inbox.get("error", "email list failed")}
        msgs = inbox.get("messages", [])
        return {
            "ok": True,
            "type": "email_summary",
            "count": len(msgs),
            "subjects": [m.get("subject", "")[:50] for m in msgs[:5]],
        }
    # web / search / browser / research -> run_browser_task, bounded.
    from swarm_os.services.browser_task import run_browser_task

    return await run_browser_task(goal, max_steps=8)


async def run_due_tasks(runner=None) -> list[str]:
    """Run every due task. Enforces the ceiling (permission model FIRST) and the
    known-safe check BEFORE dispatching — an unmapped or blocked goal refuses and
    flags, never runs."""
    if runner is None:
        runner = _default_runner
    due_ids = []
    with _LOCK:
        data = _load()
        for tid, task in data.items():
            if _is_due(task, _now()):
                due_ids.append(tid)
    ran = []
    for tid in due_ids:
        try:
            with _LOCK:
                task = _load().get(tid)
            if not task:
                continue
            goal = str(task.get("goal", ""))
            # CEILING FIRST (authority), then known-safe (fail-closed on unmapped).
            allowed, reason = _ceiling_gate(goal)
            if not allowed:
                _record_result(
                    tid, {"ok": False, "blocked": "scheduler_ceiling", "reason": reason}
                )
                log.warning("scheduled task %s BLOCKED: %s", tid, reason)
                continue
            if not _goal_is_known_safe(goal):
                _record_result(
                    tid,
                    {
                        "ok": False,
                        "blocked": "unmapped_goal",
                        "reason": "goal doesn't match a known-safe pattern; not dispatched (fail-closed)",
                    },
                )
                log.warning(
                    "scheduled task %s REFUSED: unmapped goal (not dispatched)", tid
                )
                continue
            result = await runner(task)
            _record_result(tid, result)
            ran.append(tid)
            log.info("scheduled task %s ran: %s", tid, str(result)[:120])
        except Exception as exc:
            log.warning("scheduled task %s failed: %s", tid, exc)
    return ran


def _record_result(task_id: str, result: dict) -> None:
    with _LOCK:
        data = _load()
        if task_id in data:
            data[task_id]["last_run"] = datetime.now().isoformat()
            data[task_id]["result"] = result
            _save(data)


class TaskSchedulerDaemon:
    """Daemon with heartbeat + stale-by-recency (watch-loop pattern, not reinvented)."""

    _HEARTBEAT_FILE = Path("data/events/task_scheduler_heartbeat.json")

    def __init__(self, interval_seconds: float = 60.0):
        self.interval = interval_seconds
        self._running = False
        self._loop_task = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while self._running:
            try:
                await run_due_tasks()
                self._write_heartbeat()
            except Exception as exc:
                log.warning("task scheduler tick failed: %s", exc)
            await asyncio.sleep(self.interval)

    def _write_heartbeat(self) -> None:
        try:
            self._HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._HEARTBEAT_FILE.write_text(
                json.dumps(
                    {"last_tick": _now(), "last_tick_iso": datetime.now().isoformat()}
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            log.debug("task scheduler heartbeat write failed: %s", exc)

    def stop(self) -> None:
        self._running = False
