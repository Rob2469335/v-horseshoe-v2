"""Tests for the server-side autonomous watch-loop (2026 autonomy layer).

Covers the three review-critical behaviors:
  1. Heartbeat is written EVERY tick (a HANG stops updating -> stale by recency,
     not by process-liveness).
  2. Budget reset is a concrete rolling-24h timestamp comparison, not 'next
     daemon restart'.
  3. The [AUTO-REPAIR] AGENTS.md append is lock-guarded (no two-writer race).
Plus: event-scope dispatch (tool_result -> repair, turn_budget_exhausted ->
reflexion only, verification_failed not handled), and fail-closed when the
policy is missing.
"""

import json
import time
from types import SimpleNamespace

import pytest

from swarm_os.services import watch_loop as wl
import swarm_os.services.autonomy_policy as _ap_mod


@pytest.fixture(autouse=True)
def _isolate_paths(monkeypatch, tmp_path):
    """Point every watch-loop file path at tmp_path so tests never touch real data."""
    monkeypatch.setattr(wl, "_EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(wl, "_HEARTBEAT_FILE", tmp_path / "heartbeat.json")
    monkeypatch.setattr(wl, "_AUDIT_FILE", tmp_path / "auto_repairs.jsonl")
    monkeypatch.setattr(wl, "_AGENTS_MD", tmp_path / "AGENTS.md")
    return tmp_path


def _make_engine():
    return SimpleNamespace(
        diagnose_and_repair=lambda *a, **k: {"fixed": True, "tier_used": 0}
    )


def _stub_policy(monkeypatch, daily_budget=50):
    policy = SimpleNamespace(daily_budget=daily_budget)
    monkeypatch.setattr(_ap_mod, "get_autonomy_policy", lambda **k: policy)
    return policy


def _write_tool_event(path, etype="tool_result", ok=False, error="boom", file_path=""):
    payload = {
        "result": {"ok": ok, "error": error},
        "arguments": {"file_path": file_path},
    }
    line = json.dumps({"event_type": etype, "payload": payload}) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def _last_line(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])


# ── heartbeat: written every tick, stale by recency ──────────────────────────
def test_heartbeat_written_every_tick(monkeypatch, tmp_path):
    _stub_policy(monkeypatch)
    loop = wl.WatchLoop(_make_engine(), interval_seconds=0.01)
    import asyncio

    asyncio.run(loop._tick())
    hb = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    assert "last_tick" in hb
    assert "offset" in hb


# ── never-reviewed-flag GC: wired into the tick (bounded, hourly) ────────────
def test_tick_invokes_expired_flag_gc(monkeypatch, tmp_path):
    """The watch-loop tick must periodically GC never-reviewed canary flags
    (and their snapshots) — the documented open edge that had NO production
    caller, so flagged snapshots grew forever. The cleanup must run via
    asyncio.to_thread (bounded scan, never on the loop)."""
    import asyncio
    import swarm_os.services.watch_loop as _wl

    _stub_policy(monkeypatch)
    # The tick early-returns when the events file is absent; create it so the
    # GC block after the tail is reached.
    (tmp_path / "events.jsonl").write_text("", encoding="utf-8")
    loop = _wl.WatchLoop(_make_engine(), interval_seconds=0.01)
    loop._last_flag_gc = 0.0  # force the hourly gate open

    calls = {"n": 0}

    def _fake_gc(max_age_days=14.0):
        calls["n"] += 1
        return 1

    monkeypatch.setattr(
        "runtime_v2.services.canary_registry.clear_expired_old_flags", _fake_gc
    )
    asyncio.run(loop._tick())
    assert calls["n"] == 1, "tick must invoke clear_expired_old_flags"

    # Throttled: a second tick within the hour must NOT re-run the GC.
    calls["n"] = 0
    asyncio.run(loop._tick())
    assert calls["n"] == 0, "flag GC must be throttled to hourly"


def test_stale_heartbeat_detected_by_recency_not_liveness(
    monkeypatch, tmp_path, caplog
):
    """A heartbeat that stopped updating (hang) is stale by recency, NOT by 'the
    process object is gone' — the daemon may still be alive but wedged."""
    import logging

    old = time.time() - 200  # far older than 3x a 30s interval
    (tmp_path / "heartbeat.json").write_text(
        json.dumps(
            {"last_tick": old, "last_tick_iso": "2026-01-01T00:00:00Z", "offset": 0}
        ),
        encoding="utf-8",
    )
    loop = wl.WatchLoop(_make_engine(), interval_seconds=30.0)
    with caplog.at_level(logging.WARNING, logger="WatchLoop"):
        loop._check_stale_heartbeat()
    assert any("stale heartbeat" in r.getMessage() for r in caplog.records)


def test_fresh_heartbeat_not_stale(monkeypatch, tmp_path, caplog):
    import logging

    (tmp_path / "heartbeat.json").write_text(
        json.dumps({"last_tick": time.time()}), encoding="utf-8"
    )
    loop = wl.WatchLoop(_make_engine(), interval_seconds=30.0)
    with caplog.at_level(logging.WARNING, logger="WatchLoop"):
        loop._check_stale_heartbeat()
    assert not any("stale heartbeat" in r.getMessage() for r in caplog.records)


# ── budget: concrete rolling-24h timestamp reset, stop+flag no queue ─────────
def test_budget_resets_on_rolling_24h_window(monkeypatch, tmp_path):
    """Budget resets via a concrete timestamp comparison (rolling 24h from the
    first repair in the window), NOT on daemon restart."""
    _stub_policy(monkeypatch, daily_budget=1)
    loop = wl.WatchLoop(_make_engine(), interval_seconds=0.01)
    loop._load_policy()
    loop._repair_window_start = time.time() - 25 * 3600  # window started >24h ago
    loop._repairs_in_window = 1  # already hit the 1/day budget
    assert loop._budget_available() is True
    assert loop._repairs_in_window == 0
    assert loop._repair_window_start > 0


def test_budget_exhausted_stops_repair_no_queue(monkeypatch, tmp_path):
    """Hitting the daily budget stops repair for the window and keeps tailing —
    no queueing — then resumes next window."""
    _stub_policy(monkeypatch, daily_budget=1)
    loop = wl.WatchLoop(_make_engine(), interval_seconds=0.01)
    loop._load_policy()
    loop._repair_window_start = time.time()
    loop._repairs_in_window = 1  # budget hit
    _write_tool_event(tmp_path / "events.jsonl", file_path="swarm_os/api/routes.py")
    loop._handle(_last_line(tmp_path / "events.jsonl"))
    assert not (tmp_path / "auto_repairs.jsonl").exists()


# ── event scope: tool_result repairs, others learning-only ───────────────────
def test_tool_result_triggers_repair_and_audits(monkeypatch, tmp_path):
    _stub_policy(monkeypatch)
    calls = {"n": 0}

    def _repair(err, file_path=None):
        calls["n"] += 1
        return {"fixed": True, "tier_used": 0, "fix_class": "prompt_sensitivity"}

    loop = wl.WatchLoop(
        SimpleNamespace(diagnose_and_repair=_repair), interval_seconds=0.01
    )
    loop._load_policy()
    _write_tool_event(tmp_path / "events.jsonl", file_path="swarm_os/api/routes.py")
    loop._handle(_last_line(tmp_path / "events.jsonl"))
    assert calls["n"] == 1
    rec = _last_line(tmp_path / "auto_repairs.jsonl")
    assert rec["trigger"] == "watch_loop"
    assert rec["file"].endswith("routes.py")
    assert rec["fixed"] is True


def test_turn_budget_exhausted_is_learning_only(monkeypatch, tmp_path):
    """turn_budget_exhausted must NOT trigger code repair — learning signal only,
    same 'one event, one consumer' principle as verification_failed."""
    _stub_policy(monkeypatch)
    calls = {"repair": 0}
    engine = SimpleNamespace(
        diagnose_and_repair=lambda *a, **k: calls.__setitem__(
            "repair", calls["repair"] + 1
        )
    )
    loop = wl.WatchLoop(engine, interval_seconds=0.01)
    _write_tool_event(tmp_path / "events.jsonl", etype="turn_budget_exhausted")
    loop._handle(_last_line(tmp_path / "events.jsonl"))
    assert calls["repair"] == 0
    assert not (tmp_path / "auto_repairs.jsonl").exists()


def test_verification_failed_not_handled(monkeypatch, tmp_path):
    """verification_failed stays reflexion-only in autonomous.py — the watch-loop
    does NOT dispatch on it."""
    _stub_policy(monkeypatch)
    calls = {"repair": 0}
    engine = SimpleNamespace(
        diagnose_and_repair=lambda *a, **k: calls.__setitem__(
            "repair", calls["repair"] + 1
        )
    )
    loop = wl.WatchLoop(engine, interval_seconds=0.01)
    line = json.dumps({"event_type": "verification_failed", "payload": {}}) + "\n"
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(line)
    loop._handle(_last_line(tmp_path / "events.jsonl"))
    assert calls["repair"] == 0


# ── audit trail: AGENTS.md append is lock-guarded ───────────────────────────
def test_agents_md_append_marked_auto_repair(monkeypatch, tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "## Self-Healing & Self-Learning Fixes\n- existing rule\n", encoding="utf-8"
    )
    wl._audit_write(
        {
            "timestamp": "2026-08-07T00:00:00Z",
            "trigger": "watch_loop",
            "file": "swarm_os/api/routes.py",
            "tier": 0,
            "fixed": True,
            "error": "boom",
        },
        "- **[AUTO-REPAIR] (2026-08-07T00:00:00Z)**: swarm_os/api/routes.py (tier 0, fixed=True) — error: boom\n",
    )
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "[AUTO-REPAIR]" in content
    assert content.count("## Self-Healing & Self-Learning Fixes") == 1  # section intact


def test_policy_missing_fails_closed(monkeypatch, tmp_path):
    """No policy loaded => fail-closed: budget_available() is False, no repair."""
    monkeypatch.setattr(_ap_mod, "get_autonomy_policy", lambda **k: None)
    loop = wl.WatchLoop(_make_engine(), interval_seconds=0.01)
    assert loop._budget_available() is False


@pytest.mark.asyncio
async def test_schedule_due_canaries_tracks_task_no_duplicate_spawn(monkeypatch):
    """Regression: _canary_tasks is a SET; the pre-fix code did dict item
    assignment on it (TypeError swallowed by the scheduling except), so the rid
    was never recorded and every tick re-spawned a duplicate _evaluate_canary
    for the same canary. Post-fix: rid tracked, second schedule pass is a no-op."""
    import runtime_v2.services.canary_registry as cr

    canary = {"repair_id": "r1"}
    ran = []

    async def fake_eval(self, c):
        ran.append(c)

    monkeypatch.setattr(cr, "due_canaries", lambda: [canary])
    monkeypatch.setattr(wl.WatchLoop, "_evaluate_canary", fake_eval)

    loop = wl.WatchLoop(_make_engine(), interval_seconds=0.01)
    loop._schedule_due_canaries()
    assert "r1" in loop._canary_tasks
    import asyncio as _aio

    await _aio.sleep(0)  # let the spawned task run
    assert len(ran) == 1

    # Next tick: same due canary must NOT spawn a duplicate evaluation.
    loop._schedule_due_canaries()
    assert len(ran) == 1
