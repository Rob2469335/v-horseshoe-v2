"""Tests for whole-computer self-healing:

- System probes (disk/RAM/runaway/temp/event-log) run read-only and healthy
- Destructive system signals force Governor approval_required
- Safe system signals (memory pressure) auto-execute
- RecoveryEngine dispatches system actions
- System recovery actions refuse to touch protected processes
"""
from __future__ import annotations
import sys

import pytest

from swarm_os.healing.system_probes import run_system_probes, _NEVER_TOUCH
from swarm_os.healing.system_recovery import SYSTEM_RECOVERY_ACTIONS, DESTRUCTIVE_SYSTEM_ACTIONS
from swarm_os.healing.governor import Governor
from swarm_os.healing.recovery_engine import RecoveryEngine


def test_system_probes_all_run():
    results = run_system_probes()
    for name in ("disk_space", "memory_pressure", "runaway_process", "temp_growth", "event_log_storm"):
        assert name in results
        assert "ok" in results[name]
        assert "detail" in results[name]


def test_destructive_signal_forces_approval():
    gov = Governor()
    symptom = {"component": "runaway_process", "ok": False,
               "detail": {"issue": "runaway_process", "destructive": True,
                          "processes": [{"pid": 99999, "name": "test.exe"}]}}
    decision = gov.decide(symptom)
    assert decision["mode"] == "approval_required"
    assert "destructive" in decision.get("mode_reason", "")


def test_safe_memory_signal_auto_executes():
    gov = Governor()
    symptom = {"component": "memory_pressure", "ok": False,
               "detail": {"issue": "memory_pressure", "destructive": False}}
    assert gov.decide(symptom)["mode"] == "auto_execute"


def test_recovery_engine_has_system_actions():
    eng = RecoveryEngine()
    for name in ("memory_pressure", "disk_space", "runaway_process"):
        assert name in eng.actions


def test_system_recovery_action_registry():
    assert callable(SYSTEM_RECOVERY_ACTIONS["memory_pressure"])
    assert callable(SYSTEM_RECOVERY_ACTIONS["runaway_process"])
    assert "disk_space" in DESTRUCTIVE_SYSTEM_ACTIONS
    assert "runaway_process" in DESTRUCTIVE_SYSTEM_ACTIONS
    assert "memory_pressure" not in DESTRUCTIVE_SYSTEM_ACTIONS


def test_kill_refuses_protected_process():
    from swarm_os.healing.system_recovery import kill_runaway_process
    # Simulate a runaway detection that somehow named a protected process
    symptom = {"detail": {"processes": [{"pid": sys.maxsize, "name": "explorer.exe"}]}}
    res = kill_runaway_process(symptom)
    assert res.get("ok") is False
    assert "no safe kill targets" in res.get("reason", "")


def test_free_memory_is_safe_and_runs():
    from swarm_os.healing.system_recovery import free_memory
    res = free_memory({})
    assert res.get("ok") is True
    assert res.get("action") == "free_memory"
    assert isinstance(res.get("emptied"), int)


def test_never_touch_protects_swarm_core():
    assert "llama.exe" in _NEVER_TOUCH
    assert "opencode.exe" in _NEVER_TOUCH
    assert "explorer.exe" in _NEVER_TOUCH
