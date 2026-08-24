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
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

log = logging.getLogger("WatchLoop")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EVENTS_FILE = Path("data/events/events.jsonl")
_HEARTBEAT_FILE = Path("data/events/watchman_heartbeat.json")
_AUDIT_FILE = Path("data/events/auto_repairs.jsonl")
_CANARY_HUMAN_REVIEW_FILE = Path("data/events/human_review.jsonl")
_HEARTBEAT_INTERVAL = 30.0  # seconds; matches the CLI RepairWatchman cadence
_STALE_HEARTBEAT_MULTIPLIER = 3  # a heartbeat older than 3x interval = stale
_AGENTS_MD = Path("AGENTS.md")
_RULES_MARKER = "## Self-Healing & Self-Learning Fixes\n"


def _audit_write(entry: dict, agents_md_line: str) -> None:
    """SHARED, lock-guarded audit writer for the autonomous layer.

    Every distinct autonomous event — [AUTO-REPAIR], [ROLLBACK-COMPLETED],
    [ROLLBACK-REFUSED] — goes through THIS one writer so there is exactly ONE
    writer per shared file (data/events/auto_repairs.jsonl + the AGENTS.md
    changelog). A parallel append path would reintroduce the two-writer race
    that the single-tailer watch-loop fix eliminated. Both writes use the same
    filelock primitive; never a plain open('a').
    """
    try:
        _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(_AUDIT_FILE) + ".lock", timeout=5.0)
        with lock:
            with _AUDIT_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("Audit log write failed: %s", exc)
    try:
        if not _AGENTS_MD.exists():
            return
        lock = FileLock(str(_AGENTS_MD) + ".lock", timeout=5.0)
        with lock:
            content = _AGENTS_MD.read_text(encoding="utf-8")
            if _RULES_MARKER in content:
                content = content.replace(
                    _RULES_MARKER, _RULES_MARKER + "\n" + agents_md_line, 1
                )
                _AGENTS_MD.write_text(content, encoding="utf-8")
    except Exception as exc:
        log.warning("AGENTS.md changelog append failed: %s", exc)


def _now() -> float:
    return time.time()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    for p in (_EVENTS_FILE, _HEARTBEAT_FILE, _AUDIT_FILE):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log.debug("Failed to create dir for %s: %s", p, exc)


class WatchLoop:
    def __init__(self, engine, interval_seconds: float = _HEARTBEAT_INTERVAL):
        self.engine = engine
        self.interval = interval_seconds
        self._running = False
        self._last_position = 0
        self._repair_window_start = 0.0
        self._repairs_in_window = 0
        self._policy = None
        self._canary_tasks: set = set()
        self._watch_task = None
        self._kg = None
        self._kg_lock = threading.Lock()
        self._last_flag_gc = 0.0

    def _load_policy(self):
        """Load budgets from autonomy_policy.json. None => fail-closed (no auto-repair)."""
        try:
            import swarm_os.services.autonomy_policy as _ap

            self._policy = _ap.get_autonomy_policy(reload=True)
        except Exception as exc:
            log.warning(
                "WatchLoop: policy unavailable (%s); fail-closed: no auto-repair.", exc
            )
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
            except Exception as exc:
                log.debug("Failed to stat events file: %s", exc)
        self._running = True
        self._check_stale_heartbeat()
        self._watch_task = asyncio.create_task(self._watch())

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
                    hb.get("last_tick_iso", "?"),
                    _now() - last,
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
                    json.dumps(
                        {
                            "last_tick": _now(),
                            "last_tick_iso": _iso(),
                            "offset": self._last_position,
                            "repairs_today": repairs_today,
                        }
                    ),
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
        summary = f"{str(file_path) or 'unknown'} (tier {result.get('tier_used')}, fixed={bool(result.get('fixed'))})"
        line = f"- **[AUTO-REPAIR] ({entry['timestamp']})**: {summary} — error: {str(err)[:120]}\n"
        _audit_write(entry, line)

    async def _watch(self) -> None:
        while self._running:
            try:
                await self._tick()
                # Phase B: evaluate due canaries as OFF-TICK background tasks so a
                # slow pytest re-verify never blocks the heartbeat or event tailing
                # (a slow canary must not make the daemon itself look stale).
                self._schedule_due_canaries()
            except Exception as exc:
                log.warning("WatchLoop iteration failed: %s", exc)
            await asyncio.sleep(self.interval)

    def _schedule_due_canaries(self) -> None:
        try:
            from runtime_v2.services.canary_registry import due_canaries

            due = due_canaries()
            for c in due:
                rid = c.get("repair_id")
                if rid in self._canary_tasks:
                    continue
                task = asyncio.create_task(self._evaluate_canary(c))
                self._canary_tasks.add(rid)
                task.add_done_callback(
                    lambda t, _rid=rid: self._canary_tasks.discard(_rid)
                )
        except Exception as exc:
            log.warning("WatchLoop: canary scheduling failed (%s).", exc)

    async def _evaluate_canary(self, canary: dict) -> None:
        """Run the canary re-verify (off-tick). Signal 1 (direct test re-run) is
        authoritative: a traceback-attributable failure triggers automatic rollback.
        Signal 2 (graph-based downstream inference) is human-review tier — the
        graph has a confirmed dynamic-import blind spot, so it can only add caution,
        never remove it. Unverifiable canaries flag for human attention."""
        rid = canary.get("repair_id") or ""
        file_rel = canary.get("file") or ""
        snapshot_id = canary.get("snapshot_id") or ""
        try:
            # Signal 1: re-run the repaired file's related tests.
            from pathlib import Path
            from organism_console.core.repair_engine import _run_related_tests

            fp = Path(file_rel)
            result = (
                await asyncio.to_thread(_run_related_tests, fp)
                if fp.suffix == ".py"
                else None
            )
            if result is None:
                # No related tests exist — cannot verify directly. Check signal 2.
                if self._signal2_downstream_breakage(file_rel):
                    self._resolve_flag(
                        rid,
                        "flagged",
                        "signal_2 downstream consumer breakage; HUMAN REVIEW",
                        snapshot_id,
                        file_rel,
                        human_review=True,
                    )
                elif self._soft_case_elevated_failure_rate(file_rel):
                    # SOFT CASE (L3 0.5, untested-but-sound): no single clean
                    # traceback through a dependent, but the repaired module's
                    # dependents are failing at an ELEVATED rate within the
                    # window. Correlation-based -> human review only (never
                    # auto-revert), so a fuzzy signal cannot thrash rollback.
                    self._resolve_flag(
                        rid,
                        "flagged",
                        "signal_3 elevated downstream failure rate; HUMAN REVIEW",
                        snapshot_id,
                        file_rel,
                        human_review=True,
                    )
                else:
                    self._resolve_clear(
                        rid,
                        "cleared",
                        "no related tests and no downstream breakage detected",
                    )
                return
            # _run_related_tests returns a STRUCTURED dict
            # {ok, output, flaky, initial_result, retry_result} — never a tuple.
            # Unpacking it as a tuple raised ValueError (too many values), which
            # the generic handler caught and resolved EVERY tested canary to
            # "unverifiable" — the authoritative signal-1 auto-rollback never
            # fired in the live seam.
            ok = bool(result.get("ok"))
            output = str(result.get("output", "") or "")
            if ok:
                self._resolve_clear(rid, "cleared", "related tests pass")
                return
            # Tests failed. Signal 1 is authoritative IF the failure is attributable
            # to the repaired file.
            if self._traceback_attributes(output, file_rel):
                self._resolve_flag(
                    rid,
                    "flagged",
                    f"signal_1 test regression attributable to {file_rel}",
                    snapshot_id,
                    file_rel,
                    human_review=False,
                )
            else:
                self._resolve_flag(
                    rid,
                    "flagged",
                    f"test regression NOT attributable to {file_rel}; HUMAN REVIEW",
                    snapshot_id,
                    file_rel,
                    human_review=True,
                )
        except Exception as exc:
            log.warning(
                "WatchLoop: canary %s evaluation failed (%s); flagging unverifiable.",
                rid,
                exc,
            )
            self._resolve_unverifiable(
                rid, f"canary evaluation error: {exc}", snapshot_id, file_rel
            )

    def _traceback_attributes(self, test_output: str, file_rel: str) -> bool:
        """Does the failure name the repaired file (or a module that imports it)?
        Signal 1's attribution: same module appears in the traceback.

        Match targets for repaired file `runtime_v2/services/indexer.py`:
          - the normalized path `runtime_v2/services/indexer.py` (matches both
            forward-slash relative paths and backslash-normalized absolute
            Windows paths in pytest output)
          - the dotted MODULE name `runtime_v2.services.indexer` (matches
            `import`/`from` frames that name the module itself)
        Deliberately NOT matched: the dotted PACKAGE prefix `runtime_v2.services`.
        A shorter prefix would false-attribute a failure in ANY sibling module
        (e.g. an import frame `from runtime_v2.services import other` in
        `other.py`'s traceback) to the repaired file — and since signal 1 is
        the authoritative auto-rollback trigger, that would revert the WRONG
        file. (Live bug found in the 2026 smoke test; unit fixtures only used
        forward-slash paths and missed the dotted-package collision.)"""
        import re as _re

        out = (test_output or "").replace("\\", "/")
        path = file_rel.replace("\\", "/")
        module = _re.sub(r"\.py$", "", file_rel).replace("/", ".")
        return path in out or module in out

    def _signal2_downstream_breakage(self, file_rel: str) -> bool:
        """Signal 2: does ANY recent failure name a module that imports the
        repaired file (via the cached AST KnowledgeGraph)? HUMAN-REVIEW tier — the
        graph has a confirmed dynamic-import blind spot, so this can only raise a
        flag, never auto-revert.

        Returns True only when a STATIC dependent module of the repaired file
        appears in a recent tool_result failure in the event log. Graph build
        errors and scan errors fail OPEN (return False — signal 2 only adds
        caution, never removes the direct-test signal 1)."""
        try:
            deps = self._dependent_modules(file_rel)
            if not deps:
                return False
            failures = self._recent_tool_result_failures()
            if not failures:
                return False
            haystacks = [f.replace("\\", "/") for f in failures]
            for dep in deps:
                # Match the dotted module name (runtime_v2.services.indexer) OR
                # its path form (runtime_v2/services/indexer.py) as it appears
                # in a traceback / error message.
                dotted = dep
                path_form = dep.replace(".", "/")
                for h in haystacks:
                    if dotted in h or path_form in h:
                        return True
            return False
        except Exception as exc:
            log.warning(
                "WatchLoop: signal-2 check failed (%s); treating as no breakage.", exc
            )
            return False

    def _soft_case_elevated_failure_rate(
        self, file_rel: str, threshold: int = 2
    ) -> bool:
        """SOFT-CASE signal (signal 3, human-review tier): an untested-but-sound
        repair (no related tests, L3 score 0.5) whose dependents start failing at
        an ELEVATED rate without a single clean traceback through a dependent.

        Correlation-based, so this can ONLY raise a human-review flag — never an
        automatic rollback (a fuzzy statistical trigger on correlation-not-
        causation must not thrash diff-scoped reverts).

        Detection: count recent tool_result failures that name the repaired
        module OR a static dependent module. If that count reaches `threshold`
        (default 2 — a pattern, not a single blip; single hits are signal 2's
        job), treat the rate as elevated. Fails open (False) on any error."""
        try:
            deps = self._dependent_modules(file_rel)
            if not deps:
                return False
            module = file_rel.replace("\\", "/")[:-3].replace("/", ".")
            targets = [module] + deps
            target_paths = [t.replace(".", "/") for t in targets]
            failures = self._recent_tool_result_failures()
            if not failures:
                return False
            hits = 0
            for f in failures:
                h = f.replace("\\", "/")
                if any(t in h for t in targets) or any(p in h for p in target_paths):
                    hits += 1
                    if hits >= threshold:
                        return True
            return False
        except Exception as exc:
            log.debug("WatchLoop: soft-case rate check failed (%s).", exc)
            return False

    def _dependent_modules(self, file_rel: str) -> list:
        """Dotted module names that statically import the repaired file, via the
        cached AST KnowledgeGraph. Builds the graph once (thread-safe) and
        resolves the repaired file's module name from its path."""
        if not file_rel.endswith(".py"):
            return []
        module = file_rel.replace("\\", "/")[:-3].replace("/", ".")
        try:
            kg = self._ensure_kg()
            return kg.list_dependents(module, depth=2) or []
        except Exception as exc:
            log.debug("WatchLoop: dependent-module lookup failed (%s).", exc)
            return []

    def _ensure_kg(self):
        """Build the project AST KnowledgeGraph once, thread-safely."""
        if self._kg is not None:
            return self._kg
        with self._kg_lock:
            if self._kg is not None:
                return self._kg
            from swarm_os.services.knowledge_graph import KnowledgeGraph

            kg = KnowledgeGraph(str(_PROJECT_ROOT))
            kg.build_graph()
            self._kg = kg
            return kg

    def _recent_tool_result_failures(self, limit: int = 200) -> list:
        """Recent tool_result failure error strings from the event log tail
        (bounded read — the log grows unbounded, so only the last `limit` lines
        are scanned). Returns [] on any error (signal 2 fails open)."""
        if not _EVENTS_FILE.exists():
            return []
        try:
            lines = []
            with _EVENTS_FILE.open("r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-limit:]
            for line in tail:
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                if data.get("event_type") != "tool_result":
                    continue
                res = (data.get("payload") or {}).get("result") or {}
                if res.get("ok", False):
                    continue
                err = str(res.get("error", "") or "").strip()
                if err:
                    lines.append(err)
            return lines
        except Exception as exc:
            log.debug("WatchLoop: event-tail scan failed (%s).", exc)
            return []

    def _resolve_clear(self, rid: str, state: str, detail: str) -> None:
        try:
            from runtime_v2.services.canary_registry import resolve_canary

            resolve_canary(rid, state, detail)
            log.info("Canary %s -> %s (%s)", rid, state, detail)
        except Exception as exc:
            log.warning("Canary resolve failed (%s): %s", rid, exc)

    def _resolve_flag(
        self,
        rid: str,
        state: str,
        detail: str,
        snapshot_id: str,
        file_rel: str,
        human_review: bool,
    ) -> None:
        """Flag a canary. human_review=False (signal 1 authoritative) -> automatic
        diff-scoped rollback. human_review=True (signal 2-only, or un-attributable
        test failure) -> surface for a human, do NOT auto-revert."""
        try:
            from runtime_v2.services.canary_registry import resolve_canary

            resolve_canary(rid, state, detail)
            if not human_review and snapshot_id:
                self._auto_rollback(snapshot_id, file_rel, rid, detail)
            else:
                self._flag_for_human(file_rel, rid, detail, snapshot_id)
        except Exception as exc:
            log.warning("Canary flag failed (%s): %s", rid, exc)

    def _auto_rollback(
        self, snapshot_id: str, file_rel: str, rid: str, detail: str
    ) -> None:
        """Signal-1 authoritative: restore the diff-scoped pre-repair snapshot."""
        try:
            from runtime_v2.services.run_snapshot import (
                load_run_snapshot,
                restore_run_snapshot,
            )

            snap = load_run_snapshot(snapshot_id)
            if not snap:
                log.warning(
                    "Auto-rollback %s: snapshot %s missing; cannot revert.",
                    rid,
                    snapshot_id,
                )
                self._flag_for_human(file_rel, rid, f"{detail} (snapshot missing)", "")
                return
            result = restore_run_snapshot(snap, scope=snap.get("scope"))
            entry = {
                "timestamp": _iso(),
                "trigger": "rollback",
                "repair_id": rid,
                "file": file_rel,
                "signal": "signal_1",
                "restored": result.get("restored", []),
            }
            line = f"- **[ROLLBACK-COMPLETED] ({entry['timestamp']})**: {file_rel} — {detail}\n"
            _audit_write(entry, line)
            log.warning("Auto-rolled back %s (%s)", file_rel, detail)
        except Exception as exc:
            log.warning("Auto-rollback failed (%s): %s", rid, exc)

    def _flag_for_human(
        self, file_rel: str, rid: str, detail: str, snapshot_id: str
    ) -> None:
        """Surface a human-review flag where a human will actually see it — the
        audit trail PLUS a dedicated human_review.jsonl the CLI /status reads."""
        try:
            from swarm_os.services.watch_loop import _audit_write, _iso

            entry = {
                "timestamp": _iso(),
                "trigger": "rollback_human_review",
                "repair_id": rid,
                "file": file_rel,
                "detail": detail,
                "snapshot_id": snapshot_id,
            }
            line = f"- **[CANARY-FLAGGED: human review] ({entry['timestamp']})**: {file_rel} — {detail}\n"
            _audit_write(entry, line)
            import json as _json

            f = _CANARY_HUMAN_REVIEW_FILE
            f.parent.mkdir(parents=True, exist_ok=True)
            lock = FileLock(str(f) + ".lock", timeout=5.0)
            with lock:
                with f.open("a", encoding="utf-8") as fh:
                    fh.write(_json.dumps(entry) + "\n")
            log.warning("CANARY-FLAGGED for human review: %s — %s", file_rel, detail)
        except Exception as exc:
            log.warning("Human-review flag failed (%s): %s", rid, exc)

    def _resolve_unverifiable(
        self, rid: str, detail: str, snapshot_id: str, file_rel: str
    ) -> None:
        try:
            from runtime_v2.services.canary_registry import resolve_canary

            resolve_canary(rid, "unverifiable", detail)
            self._flag_for_human(file_rel, rid, f"UNVERIFIABLE: {detail}", snapshot_id)
        except Exception as exc:
            log.warning("Canary unverifiable resolve failed (%s): %s", rid, exc)

    async def _tick(self) -> None:
        self._main_loop = asyncio.get_running_loop()
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
                    except Exception as exc:
                        log.debug("Failed to parse event line: %s", exc)
                        continue
                    await asyncio.to_thread(self._handle, data)
        except Exception as exc:
            log.warning("WatchLoop tail failed: %s", exc)
        self._write_heartbeat(self._repairs_in_window)
        # Stated-open-edge GC: never-reviewed flags (and their snapshots) expire
        # after 14 days. Bounded scan — run at most hourly so it never competes
        # with the tail/heartbeat on every tick.
        if _now() - self._last_flag_gc > 3600:
            self._last_flag_gc = _now()
            try:
                from runtime_v2.services.canary_registry import clear_expired_old_flags

                expired = await asyncio.to_thread(clear_expired_old_flags)
                if expired:
                    log.info(
                        "WatchLoop: expired %d never-reviewed canary flag(s).", expired
                    )
            except Exception as exc:
                log.debug("WatchLoop: flag GC skipped (%s).", exc)

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
                _now() - self._repair_window_start,
                self._repairs_in_window,
                err[:80],
            )
            return
        try:
            # 2026 autonomy move 4 Phase A: capture the PRE-repair worktree state
            # BEFORE the repair writes. Ordering is the entire mechanism: snapshot
            # captured after the repair would hold post-repair bytes, so a restore
            # would be a silent no-op while every test still passes. Captured
            # here (before diagnose_and_repair), stored durably, and its id is
            # threaded into the audit record so a regression can find the exact
            # pre-state to revert.
            snapshot_id = self._capture_repair_snapshot(fpath)
            if hasattr(self.engine, "diagnose_and_repair"):
                result = self.engine.diagnose_and_repair(err, file_path=fpath)
            elif hasattr(self.engine, "repair"):
                result = self.engine.repair(err, file_path=fpath)
            else:
                result = {}
            result["snapshot_id"] = snapshot_id
            self._record_repair()
            self._audit_repair(err, fpath, result or {})
            # Phase B: register a canary so a later regression within the window
            # triggers rollback (signal 1 -> auto, signal 2-only -> human review).
            if fpath and snapshot_id and result.get("fixed"):
                self._register_canary(fpath, snapshot_id)
        except Exception as exc:
            log.warning("WatchLoop: repair dispatch failed (%s).", exc)

    def _register_canary(self, file_path, snapshot_id: str) -> None:
        try:
            from pathlib import Path as _P
            from runtime_v2.services.canary_registry import register_canary

            root = _P.cwd()
            try:
                file_rel = str(
                    _P(file_path).resolve().relative_to(root.resolve())
                ).replace("\\", "/")
            except Exception as exc:
                log.debug("Failed to resolve relative path for canary: %s", exc)
                file_rel = str(file_path)
            ok, msg = register_canary(file_rel, snapshot_id, policy=self._policy)
            if not ok:
                log.warning("Canary registration refused for %s: %s", file_rel, msg)
            else:
                log.info("Registered canary %s for %s (30-min window)", msg, file_rel)
        except Exception as exc:
            log.warning("WatchLoop: canary registration failed (%s).", exc)

    def _capture_repair_snapshot(self, file_path) -> str:
        """Capture the current worktree as a durable, diff-scoped repair snapshot
        (Phase A: capture + revert together). Scope = the repair's single target
        file — the repair engine is single-file by construction (every tier writes
        only the `file_path` dispatched on), so intended == actual write scope."""
        try:
            from organism_console._commands_opencode import snapshot_worktree
            from runtime_v2.services.run_snapshot import (
                build_repair_snapshot,
                write_run_snapshot,
            )
            from pathlib import Path as _P

            root = _P.cwd()
            scope = []
            if file_path:
                try:
                    scope = [
                        str(
                            _P(file_path).resolve().relative_to(root.resolve())
                        ).replace("\\", "/")
                    ]
                except Exception as exc:
                    log.debug("Failed to resolve relative path for snapshot: %s", exc)
                    scope = [str(file_path)]
            snap = build_repair_snapshot(snapshot_worktree(root), scope=scope)
            return write_run_snapshot(snap)
        except Exception as exc:
            log.warning(
                "WatchLoop: repair snapshot capture failed (%s); repair proceeds without a revert point.",
                exc,
            )
            return ""

    def _handle_turn_budget(self, data: dict) -> None:
        """Learning-only (no repair dispatch) — same principle as verification_failed:
        a turn-budget signal is not a code defect. Writes a reflexion so the next run
        gets a [PAST-MISTAKE WARNING]."""
        try:
            payload = data.get("payload") or {}
            agent_id = payload.get("agent_id") or data.get("source") or "unknown"
            prompt = str(payload.get("prompt") or "")[:150]
            log.warning(
                "WatchLoop: turn_budget_exhausted for agent %s (prompt: %s)",
                agent_id,
                prompt,
            )
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

            def _consume(_t: asyncio.Task) -> None:
                if not _t.cancelled() and _t.exception():
                    log.warning(
                        "WatchLoop: turn-budget reflexion task failed (%s).",
                        _t.exception(),
                    )

            if getattr(self, "_main_loop", None) and self._main_loop.is_running():
                def _spawn():
                    task = self._main_loop.create_task(_record())
                    task.add_done_callback(_consume)
                self._main_loop.call_soon_threadsafe(_spawn)
            else:
                try:
                    _record_task = asyncio.get_running_loop().create_task(_record())
                    _record_task.add_done_callback(_consume)
                except RuntimeError:
                    pass
        except Exception as exc:
            log.warning("WatchLoop: turn-budget reflexion failed (%s).", exc)
