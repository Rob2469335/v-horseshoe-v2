"""Regression tests for recovery_engine self-restart + telemetry fixes.

Covers the Phase 3 fixes:
- restart_backend refuses an IN-PROCESS self-restart (would otherwise spawn a
  child uvicorn that hits EADDRINUSE on :8000 while the parent still owns it).
- restart failure/early-exit paths carry an explicit `action` key so the
  Governor's finalize() can record strategy stats (it only updates `if comp and
  action`, governor.py).
- micro_restart dispatches an async action ONCE (not leak the first awaitable
  and execute a second time).
"""
from __future__ import annotations
import sys

import pytest


def test_restart_backend_refuses_in_process(monkeypatch):
    """When the calling process IS the backend (sys.argv contains
    swarm_os.app.main:app), restart_backend must refuse fail-closed instead of
    spawning a doomed child on the still-held :8000 port."""
    monkeypatch.setattr(sys, "argv", ["python", "-m", "uvicorn", "swarm_os.app.main:app"])
    from swarm_os.healing.recovery_engine import restart_backend
    res = restart_backend({})
    assert res.get("ok") is False
    assert res.get("action") == "restart_backend"
    assert "ADDRINUSE" in res.get("error", "")


def test_restart_backend_allows_external_process(monkeypatch):
    """Called from a non-backend process (the CLI healing watchman), the
    cross-process kill+relaunch is legitimate — not refused."""
    monkeypatch.setattr(sys, "argv", ["rob", "heal", "run"])
    import swarm_os.healing.recovery_engine as re
    # Prevent the real kill/spawn side-effects: patch the internals.
    monkeypatch.setattr(re, "_find_and_kill", lambda match, exclude_pid=None: [])

    class _FakeProc:
        def poll(self):
            return None  # keep running — not an early-exit
        def wait(self, timeout):
            return None
        @property
        def returncode(self):
            return None

    monkeypatch.setattr(re.subprocess, "Popen", lambda *a, **k: _FakeProc())
    res = re.restart_backend({})
    assert res.get("ok") is True
    assert res.get("action") == "restarted_backend"


def test_restart_failure_paths_carry_action(monkeypatch):
    """Early-exit failure paths must include the action key so Governor
    finalize() records strategy stats instead of silently dropping them."""
    import swarm_os.healing.recovery_engine as re

    class _FakeProc:
        def poll(self):
            return 1  # exited with code 1 -> early-exit failure path
        def wait(self, timeout):
            return None
        @property
        def returncode(self):
            return 1

    monkeypatch.setattr(sys, "argv", ["rob", "heal"])
    monkeypatch.setattr(re, "_find_and_kill", lambda match, exclude_pid=None: [])
    monkeypatch.setattr(re.subprocess, "Popen", lambda *a, **k: _FakeProc())
    res = re.restart_backend({})
    assert res.get("ok") is False
    assert res.get("action") == "restart_backend"


def test_micro_restart_async_action_runs_once():
    """A sync-def action that returns an awaitable must execute exactly ONCE
    (no leaked first coroutine, no second invocation)."""
    import asyncio
    from swarm_os.healing.recovery_engine import micro_restart

    calls = {"n": 0}

    def async_style_action(anomaly):
        kills = []
        calls["n"] += 1
        async def _impl():
            await asyncio.sleep(0)
            return {"ok": True, "killed": kills}
        return _impl()

    result = micro_restart(
        {"component": "backend", "level": "forecast_warning"},
        actions={"backend": async_style_action},
    )
    # micro_restart returns either a coroutine/future or the awaited dict.
    if hasattr(result, "__await__"):
        result = asyncio.run(result)

    assert calls["n"] == 1
    assert result.get("ok") is True
    assert "micro_restart" in result.get("action", "")


@pytest.mark.asyncio
async def test_recover_dispatches_sync_action_off_loop(monkeypatch):
    """CON-5: a SYNC recovery action (restart_llamacpp / restart_backend, which
    subprocess.Popen().wait() up to 2s) must be dispatched via asyncio.to_thread
    so it never blocks the event loop."""
    import threading
    from swarm_os.healing.recovery_engine import RecoveryEngine

    ran_in = {}

    def blocking_sync_action(anomaly):
        ran_in["thread"] = threading.get_ident()
        return {"ok": True, "action": "sync_done"}

    engine = RecoveryEngine(actions={"llamacpp": blocking_sync_action})
    main_thread = threading.get_ident()
    result = await engine.recover({"component": "llamacpp"})
    assert result.get("ok") is True
    assert result.get("action") == "sync_done"
    # The sync action must have run on a worker thread, not the event-loop thread.
    assert ran_in["thread"] != main_thread
