"""event_log_storm is report-only: it must route to human review, never to an
LLM-generated auto-recovery.

Regression (2026-09-11): the healing engine reported an `event_log_storm`
(errors: 20) whose recovery "did not execute successfully". Root cause:
`SYSTEM_RECOVERY_ACTIONS` had NO `event_log_storm` entry, so RecoveryEngine fell
through to `llm_guided_recovery`, which generated a script, tripped the sandbox
Security Gate, and was logged as a failed auto-repair. The probe's own contract
is "report-only; the remedy is human judgement, not an auto-kill".
"""

from __future__ import annotations

import asyncio

from swarm_os.healing.recovery_engine import RecoveryEngine, llm_guided_recovery
from swarm_os.healing.system_recovery import (
    SYSTEM_RECOVERY_ACTIONS,
    flag_for_human_review,
)


def test_event_log_storm_has_explicit_report_only_mapping():
    assert SYSTEM_RECOVERY_ACTIONS.get("event_log_storm") is flag_for_human_review


def test_flag_for_human_review_returns_human_review():
    res = flag_for_human_review({"component": "event_log_storm", "detail": {}})
    assert res["ok"] is False
    assert res["action"] == "human_review_required"


def test_recovery_engine_does_not_llm_recover_event_log_storm(monkeypatch):
    calls = {"llm": 0}

    async def _boom(anomaly):
        calls["llm"] += 1
        raise AssertionError("llm_guided_recovery must NOT run for a report-only issue")

    monkeypatch.setattr(
        "swarm_os.healing.recovery_engine.llm_guided_recovery", _boom
    )
    engine = RecoveryEngine()
    result = asyncio.run(engine.recover({"component": "event_log_storm"}))
    assert calls["llm"] == 0
    assert result["action"] == "human_review_required"
    # sanity: the real LLM fallback still exists for genuinely recoverable issues
    assert callable(llm_guided_recovery)
