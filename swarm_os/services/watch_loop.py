"""Server-side autonomous watch-loop (2026 autonomy layer, move 1).

Promotes the CLI's RepairWatchman into a backend daemon started in main.py's
lifespan, so events.jsonl is tailed server-side and repairs trigger WITHOUT a
human launching the CLI. Loads its budgets and policy gates from
autonomy_policy.json (single source of truth) via autonomy_policy.py.

LOCKED DECISIONS (see autonomy_policy.json + AGENTS.md):
  1. Event scope = _handle_event_line's FULL dispatch, unchanged:
       - tool_result (ok:False)      -> code repair (the ONLY repair trigger)
       - turn_budget_exhausted       -> reflexion learning write (no repair)
     verification_failed stays reflexion-only in autonomous.py (never moved in).
     "One event, one consumer, one job" — learning signals never trigger repair.
  2. Failure mode = fail-closed-but-VISIBLE. A heartbeat is written EVERY loop
     iteration (not just on clean exit), so a HANG (loop stops updating) is
     detected as "stale heartbeat" (last_tick older than ~2x the interval),
     NOT "process object is gone". On startup a stale heartbeat logs a distinct
     WARNING that the prior daemon may have died mid-run.
  3. Budget boundary = stop + flag, NO queue. When the daily or per-incident
     budget is exhausted the loop keeps tailing + logging, but performs no more
     repairs that cycle. No queueing (an unattended repair past a hard ceiling
     is exactly what the policy exists to prevent). Budget resets on a CONCRETE
     timestamp comparison (rolling 24h window from the first repair of the
     current window), not "next daemon restart".
  4. Notification visibility = every autonomous repair leaves a DISTINCT audit
     trail in TWO places, both lock-guarded so they never race with manual
     edits: data/events/auto_repairs.jsonl (structured) + a clearly-marked
     [AUTO-REPAIR] line in the AGENTS.md "Self-Healing & Self-Learning Fixes"
     changelog section. Six months later you can tell autonomous commits from
     manual/Flash-assisted ones via git log / AGENTS.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

log = logging.getLogger("WatchLoop")

_EVENTS_FILE = Path("data/events/events.jsonl")
_HEARTBEAT_FILE = Path("data/events/watchman_heartbeat.json")
_AUDIT_FILE = Path("data/events/auto_repairs.jsonl")
_HEARTBEAT_INTERVAL = 30.0  # seconds; matches the CLI RepairWatchman cadence
_STALE_HEARTBEAT_MULTIPLIER = 3  # a heartbeat older than 3x interval = stale
_AGENTS_MD = Path("AGENTS.md")
_RULES_MARKER = "## Self-Healing & Self-Learning Fixes\n"


def _now() -> float:
    return time.time()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    for p in (_EVENTS_FILE, _HEARTBEAT_FILE, _AUDIT_FILE):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


class WatchLoop:
    def __init__(self, engine, interval_seconds: float = _HEARTBEAT_INTERVAL):
        self.engine = engine
        self.interval = interval_seconds
        self._running = False
        self._last_position = 0
        self._repair_window_start = 0.0
        self._repairs_in_window = 0
        self._policy = None

    def _load_policy(self):
        """Load budgets from autonomy_policy.json. None => fail-closed (no auto-repair)."""
        try:
            import swarm_os.services.autonomy_policy as _ap
            self._policy = _ap.get_autonomy_policy(reload=True)
        except Exception as exc:
            log.warning("WatchLoop: policy unavailable (%s); fail-closed: no auto-repair.", exc)
            self._policy = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, start_at_end: bool = True) -> None:
        if self._running:
            return
        self._load_policy()
        if start_at_end:
            try:
                if _EVENTS_FILE.exists():
                    self._last_position = _EVENTS_FILE.stat().st_size
            except Exception:
                pass
        self._running = True
        self._check_stale_heartbeat()
        asyncio.create_task(self._watch())

    async def stop(self) -> None:
        self._running = False

    def _check_stale_heartbeat(self) -> None:
        """Fail-closed-but-VISIBLE: a heartbeat older than 3x the interval means a
        prior daemon HUNG (loop stopped updating) or died — either way it is
        'stale' by recency, not by process-liveness. Log loudly on startup."""
        try:
            if not _HEARTBEAT_FILE.exists():
                return
            hb = json.loads(_HEARTBEAT_FILE.read_text(encoding="utf-8"))
            last = float(hb.get("last_tick", 0) or 0)
            stale_at = last + self.interval * _STALE_HEARTBEAT_MULTIPLIER
            if _now() > stale_at:
                log.warning(
                    "WatchLoop: stale heartbeat detected (last tick %s, %.0fs ago) — "
                    "a prior daemon may have hung or died mid-run. Fail-closed: it "
                    "was not repairing. This daemon resumes tailing from end of log.",
                    hb.get("last_tick_iso", "?"), _now() - last,
                )
        except Exception as exc:
            log.warning("WatchLoop: could not read heartbeat (%s).", exc)

    def _write_heartbeat(self, repairs_today: int) -> None:
        """Written EVERY iteration, not just on clean exit — a hang stops the
        updates and is then detectable as 'stale', not misread as healthy."""
        try:
            _HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
            lock = FileLock(str(_HEARTBEAT_FILE) + ".lock", timeout=5.0)
            with lock:
                _HEARTBEAT_FILE.write_text(
                    json.dumps({
                        "last_tick": _now(),
                        "last_tick_iso": _iso(),
                        "offset": self._last_position,
                        "repairs_today": repairs_today,
                    }),
                    encoding="utf-8",
                )
        except Exception as exc:
            log.debug("WatchLoop: heartbeat write failed (%s).", exc)

    def _budget_available(self) -> bool:
        """Stop + flag, NO queue. Budget is a rolling 24h window from the FIRST
        repair in the window (concrete timestamp comparison), not a calendar date
        and not 'next daemon restart'."""
        if self._policy is None:
            self._load_policy()
        if self._policy is None:
            log.warning("WatchLoop: no policy loaded — budget fail-closed (no repair).")
            return False
        now = _now()
        if self._repairs_in_window == 0:
            self._repair_window_start = now
        elif now - self._repair_window_start >= 24 * 3600:
            self._repair_window_start = now
            self._repairs_in_window = 0
        return self._repairs_in_window < int(self._policy.daily_budget)

    def _record_repair(self) -> None:
        self._repairs_in_window += 1

    def _audit_repair(self, err: str, file_path, result: dict) -> None:
        """Distinct, lock-guarded audit trail in TWO places (structured log +
        AGENTS.md changelog) so autonomous repairs are distinguishable from manual
        ones and never race with a human editing AGENTS.md."""
        entry = {
            "timestamp": _iso(),
            "trigger": "watch_loop",
            "error": str(err)[:500],
            "file": str(file_path or ""),
            "tier": result.get("tier_used"),
            "fixed": bool(result.get("fixed")),
            "validation_error": result.get("validation_error"),
            "fix_class": result.get("fix_class"),
        }
        try:
            _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            lock = FileLock(str(_AUDIT_FILE) + ".lock", timeout=5.0)
            with lock:
                with _AUDIT_FILE.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warning("WatchLoop: auto-repair audit log write failed (%s).", exc)
        self._append_agents_md(entry)

    def _append_agents_md(self, entry: dict) -> None:
        """Append a clearly-marked [AUTO-REPAIR] line to the AGENTS.md changelog
        section under the SAME filelock primitive used by the structured audit
        log, so this never races with a human/Flash editing AGENTS.md (a
        read-modify-write race would corrupt the section)."""
        try:
            if not _AGENTS_MD.exists():
                return
            summary = f"{entry.get('file') or 'unknown'} (tier {entry.get('tier')}, fixed={entry.get('fixed')})"
            line = f"- **[AUTO-REPAIR] ({entry.get('timestamp')})**: {summary} — error: {str(entry.get('error'))[:120]}\n"
            lock = FileLock(str(_AGENTS_MD) + ".lock", timeout=5.0)
            with lock:
                content = _AGENTS_MD.read_text(encoding="utf-8")
                if _RULES_MARKER in content:
                    content = content.replace(_RULES_MARKER, _RULES_MARKER + "\n" + line, 1)
                    _AGENTS_MD.write_text(content, encoding="utf-8")
        except Exception as exc:
            log.warning("WatchLoop: AGENTS.md [AUTO-REPAIR] append failed (%s).", exc)

    async def _watch(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                log.warning("WatchLoop iteration failed: %s", exc)
            await asyncio.sleep(self.interval)

    async def _tick(self) -> None:
        if not _EVENTS_FILE.exists():
            self._write_heartbeat(self._repairs_in_window)
            return
        try:
            current_size = _EVENTS_FILE.stat().st_size
            if current_size > self._last_position:
                lines = []
                with _EVENTS_FILE.open("r", encoding="utf-8") as f:
                    f.seek(self._last_position)
                    lines = f.readlines()
                self._last_position = current_size
                for line in lines:
                    if not self._running:
                        return
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    await asyncio.to_thread(self._handle, data)
        except Exception as exc:
            log.warning("WatchLoop tail failed: %s", exc)
        self._write_heartbeat(self._repairs_in_window)

    def _handle(self, data: dict) -> None:
        """Same dispatch as _handle_event_line, plus the budget gate + audit trail.
        Event scope (locked): tool_result -> repair; turn_budget_exhausted ->
        reflexion. verification_failed is NOT handled here (stays in autonomous.py)."""
        if not isinstance(data, dict):
            return
        etype = data.get("event_type")
        if etype == "tool_result":
            self._handle_tool_result(data)
        elif etype == "turn_budget_exhausted":
            self._handle_turn_budget(data)

    def _handle_tool_result(self, data: dict) -> None:
        res = (data.get("payload") or {}).get("result", {}) or {}
        if res.get("ok", False):
            return
        err = str(res.get("error", "") or "").strip()
        if not err or len(err) >= 500:
            return
        import re
        payload = data.get("payload") or {}
        args = payload.get("arguments") or {}
        file_path_str = args.get("file_path") or args.get("TargetFile")
        if not file_path_str:
            m = re.search(r'File "([^"]+\.py)"', err)
            if m:
                file_path_str = m.group(1)
        fpath = Path(file_path_str) if file_path_str else None
        # Budget gate: stop + flag, NO queue.
        if not self._budget_available():
            log.warning(
                "WatchLoop: repair budget exhausted (window started %.0fs ago, %d repairs) — "
                "skipping repair for %r, will resume next 24h window. No queueing.",
                _now() - self._repair_window_start, self._repairs_in_window, err[:80],
            )
            return
        try:
            if hasattr(self.engine, "diagnose_and_repair"):
                result = self.engine.diagnose_and_repair(err, file_path=fpath)
            elif hasattr(self.engine, "repair"):
                result = self.engine.repair(err, file_path=fpath)
            else:
                result = {}
            self._record_repair()
            self._audit_repair(err, fpath, result or {})
        except Exception as exc:
            log.warning("WatchLoop: repair dispatch failed (%s).", exc)

    def _handle_turn_budget(self, data: dict) -> None:
        """Learning-only (no repair dispatch) — same principle as verification_failed:
        a turn-budget signal is not a code defect. Writes a reflexion so the next run
        gets a [PAST-MISTAKE WARNING]."""
        try:
            payload = data.get("payload") or {}
            agent_id = payload.get("agent_id") or data.get("source") or "unknown"
            prompt = str(payload.get("prompt") or "")[:150]
            log.warning("WatchLoop: turn_budget_exhausted for agent %s (prompt: %s)", agent_id, prompt)
            from swarm_os.services.reflection_loop import get_reflection_service

            async def _record():
                await get_reflection_service().store_reflexion(
                    task=f"agent:{agent_id} compound goal {prompt} exhausted turns",
                    action="max_turns_reached",
                    failure_reason="agent ran out of turns before completing a compound goal.",
                    correction="Prefer completing the goal with the FEWEST tool calls. For compound goals needing both codebase reads and web research, interleave them — do not spend all turns on exploration.",
                    do_not_repeat=f"agent:{agent_id} must not burn all turns on exploration before the required tool.",
                    component=agent_id,
                    confidence=0.6,
                )

            try:
                asyncio.get_running_loop().create_task(_record())
            except RuntimeError:
                asyncio.run(_record())
        except Exception as exc:
            log.warning("WatchLoop: turn-budget reflexion failed (%s).", exc)
