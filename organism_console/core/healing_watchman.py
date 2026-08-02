"""Background healing watchman for the CLI.

Ticks the backend healing loop (FailureDetector -> Governor -> RecoveryEngine)
on a fixed cadence so infrastructure health is self-healed even when no goal
loop is running. Only governor-approved modes (auto_execute / sandbox_first)
trigger an actual recovery; everything else is surfaced as a notification.

Every executed recovery is finalized back through Governor.finalize() so the
learner records the true outcome (previously the outcome-learning loop was dead).
"""
import logging
import threading
import time

from swarm_os.healing.failure_detector import run_coro_sync
from swarm_os.healing.recovery_engine import RecoveryEngine

log = logging.getLogger("zenith_healing_watchman")


class HealingWatchman:
    def __init__(self, interval_seconds: float = 60.0, console=None):
        self.interval = interval_seconds
        self.console = console
        self._running = False
        self._thread = None
        self._loop = None

    def _get_loop(self):
        from swarm_os.healing.healing_loop import HealingLoop
        if self._loop is None:
            self._loop = HealingLoop()
        return self._loop

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="healing-watchman", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def _notify(self, message: str):
        if self.console is not None:
            try:
                self.console.print(message)
            except Exception:
                pass
        log.info(message)

    def _tick(self):
        loop = self._get_loop()
        heal_result = loop.tick()
        if heal_result.get("status") != "healing_decision":
            return

        decision = heal_result.get("decision", {})
        mode = decision.get("mode", "unknown")
        component = heal_result.get("component", "unknown")
        reasoning = decision.get("reasoning", "")
        self._notify(f"[bold yellow]⚕ Self-healing:[/bold yellow] issue detected in [cyan]{component}[/cyan] (mode: {mode})")

        if mode not in ("auto_execute", "sandbox_first"):
            if mode == "approval_required":
                self._notify(f"[bold yellow]🔧 Approval required:[/bold yellow] [{component}] {reasoning}")
            if mode == "reject":
                self._notify(f"[bold red]⛔ Rejected by Governor:[/bold red] [{component}] {reasoning}")
            return

        symptom = (heal_result.get("all_signals") or [{}])[0] or {"component": component}
        try:
            result = run_coro_sync(RecoveryEngine().recover(symptom), timeout=300.0)
        except Exception as exc:
            log.warning("Background recovery failed to run: %s", exc)
            result = {"ok": False, "error": str(exc)}
        if result and result.get("ok"):
            self._notify(f"[bold green]✓ Auto-recovered:[/bold green] {result.get('action')}")
            # Whole-computer healing learns: persist a grounded reflexion rule so a
            # recurring system issue injects a [PAST-MISTAKE WARNING] into future runs.
            self._store_system_lesson(component, symptom, result)
        else:
            detail = (result or {}).get("error") or (result or {}).get("reason") or "no recovery result"
            self._notify(f"[bold red]✗ Auto-recovery failed:[/bold red] {detail}")
        try:
            loop.finalize(decision, result)
        except Exception as exc:
            log.warning("Failed to finalize healing incident: %s", exc)

    def _store_system_lesson(self, component: str, symptom: dict, result: dict):
        """Persist a grounded (non-LLM) reflexion rule after a successful
        whole-computer recovery, so check_for_past_mistakes() steers future runs
        with a [PAST-MISTAKE WARNING] the next time the same system issue appears.
        Mirrors the tool-failure reflexion loop in agent_service_v2."""
        detail = symptom.get("detail") or {}
        issue = detail.get("issue") if isinstance(detail, dict) else component
        action = result.get("action") or "recovered"
        corrections = {
            "memory_pressure": "Check memory pressure; empty working sets of non-critical processes to relieve RAM (free_memory) before escalating.",
            "disk_space": "Check disk usage; clean stale temp files (>24h) in the OS temp folder when a drive exceeds 90%.",
            "runaway_process": "Identify the runaway process by pid/name, confirm it is not system-critical, then terminate it gracefully.",
            "temp_growth": "Check temp folder growth; remove stale files older than 24h outside protected cache subdirs.",
            "stopped_service": "Restart the stopped Windows service by its exact service_name from the signal detail.",
        }
        correction = corrections.get(issue, f"Recurring system issue '{issue}' was resolved via {action}; re-check the machine before proceeding.")
        do_not = f"Do NOT ignore repeated '{issue}' signals — a prior recovery used {action}."
        try:
            from swarm_os.services.reflection_loop import get_reflection_service
            from swarm_os.healing.failure_detector import run_coro_sync

            async def _store():
                await get_reflection_service().store_reflexion(
                    task=f"agent:healing system {issue}",
                    action=f"system:{action}",
                    failure_reason=f"system {issue} detected via probe",
                    correction=correction,
                    do_not_repeat=do_not,
                    component=f"system:{issue}",
                    confidence=0.75,
                )
            run_coro_sync(_store(), timeout=30.0)
            log.info("Stored system healing lesson for '%s'", issue)
        except Exception as exc:
            log.warning("Failed to store system healing lesson: %s", exc)

    def _run(self):
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                log.warning("HealingWatchman iteration failed: %s", exc)
            time.sleep(self.interval)
