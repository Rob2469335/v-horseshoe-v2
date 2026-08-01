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
            elif mode == "reject":
                self._notify(f"[bold red]⛔ Rejected by Governor:[/bold red] [{component}] {reasoning}")
            return

        from swarm_os.healing.failure_detector import run_coro_sync
        from swarm_os.healing.recovery_engine import RecoveryEngine
        symptom = (heal_result.get("all_signals") or [{}])[0] or {"component": component}
        try:
            result = run_coro_sync(RecoveryEngine().recover(symptom), timeout=300.0)
        except Exception as exc:
            log.warning("Background recovery failed to run: %s", exc)
            result = {"ok": False, "error": str(exc)}
        if result and result.get("ok"):
            self._notify(f"[bold green]✓ Auto-recovered:[/bold green] {result.get('action')}")
        else:
            detail = (result or {}).get("error") or (result or {}).get("reason") or "no recovery result"
            self._notify(f"[bold red]✗ Auto-recovery failed:[/bold red] {detail}")
        try:
            loop.finalize(decision, result)
        except Exception as exc:
            log.warning("Failed to finalize healing incident: %s", exc)

    def _run(self):
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                log.warning("HealingWatchman iteration failed: %s", exc)
            time.sleep(self.interval)
