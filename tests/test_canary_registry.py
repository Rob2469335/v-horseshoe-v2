"""Tests for Phase B of signal-gated rollback (canary registry + off-tick eval).

The critical behaviors:
  1. Registry is durable (restart-survival) + lock-guarded.
  2. One pending canary per file (refuse second registration) — matches Phase A's
     conflict shape, so a flagged rollback always knows which snapshot to restore.
  3. Off-tick evaluation: a canary re-verify never blocks the heartbeat/tailing.
  4. Signal 1 (direct test regression, attributable) -> AUTOMATIC diff-scoped
     rollback. Signal 2-only (graph-based, has a dynamic-import blind spot) ->
     HUMAN REVIEW, never automatic.
  5. Unverifiable canary -> flag for human, never assume clean.
  6. Never-reviewed flags eventually expire (bounded snapshot growth).
"""
from types import SimpleNamespace

import pytest

from runtime_v2.services import canary_registry as cr
from swarm_os.services import watch_loop as wl


@pytest.fixture(autouse=True)
def _isolate_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(cr, "_REGISTRY_FILE", tmp_path / "canary_pending.json")
    monkeypatch.setattr(wl, "_EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(wl, "_HEARTBEAT_FILE", tmp_path / "heartbeat.json")
    monkeypatch.setattr(wl, "_AUDIT_FILE", tmp_path / "auto_repairs.jsonl")
    monkeypatch.setattr(wl, "_AGENTS_MD", tmp_path / "AGENTS.md")
    monkeypatch.setattr(wl, "_CANARY_HUMAN_REVIEW_FILE", tmp_path / "human_review.jsonl")
    return tmp_path


# ── registry durability + one-canary-per-file ───────────────────────────────
def test_register_and_load_roundtrip(tmp_path):
    ok, rid = cr.register_canary("swarm_os/api/routes.py", "snap1", window_minutes=0.01)
    assert ok
    reg = cr.load_registry()
    assert reg[rid]["file"] == "swarm_os/api/routes.py"
    assert reg[rid]["snapshot_id"] == "snap1"
    assert reg[rid]["state"] == cr.PENDING


def test_refuse_second_pending_canary_same_file(tmp_path):
    ok1, rid1 = cr.register_canary("swarm_os/api/routes.py", "snapA", window_minutes=5)
    assert ok1
    ok2, msg2 = cr.register_canary("swarm_os/api/routes.py", "snapB", window_minutes=5)
    assert ok2 is False
    assert "already pending" in msg2
    # Different file registers fine.
    ok3, _ = cr.register_canary("runtime_v2/api/agent_service_v2.py", "snapC", window_minutes=5)
    assert ok3


def test_registry_restart_survival(tmp_path):
    ok, rid = cr.register_canary("swarm_os/api/routes.py", "snap1", window_minutes=5)
    assert ok
    # A fresh load (simulating daemon restart) still sees the pending canary.
    reg = cr.load_registry()
    assert reg[rid]["state"] == cr.PENDING


def test_due_canaries_returns_passed_due_at(tmp_path):
    ok, rid = cr.register_canary("swarm_os/api/routes.py", "snap1", window_minutes=-1)
    assert ok  # negative window => already due
    due = cr.due_canaries()
    assert any(c["repair_id"] == rid for c in due)


def test_resolve_canary_terminal_state(tmp_path):
    ok, rid = cr.register_canary("swarm_os/api/routes.py", "snap1", window_minutes=5)
    cr.resolve_canary(rid, cr.CLEARED, "related tests pass")
    reg = cr.load_registry()
    assert reg[rid]["state"] == cr.CLEARED
    assert reg[rid]["resolved_at"]


# ── off-tick scheduling ─────────────────────────────────────────────────────
def test_schedule_creates_task_without_blocking(tmp_path):
    """A due canary is scheduled as a background task — the tick returns
    immediately, so a slow pytest re-verify cannot block the heartbeat."""
    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    loop._canary_tasks = set()
    cr.register_canary("swarm_os/api/routes.py", "snap1", window_minutes=0.01)
    loop._schedule_due_canaries()
    # The registry still has it pending (evaluation runs in a background task).
    assert cr.pending_canaries()  # pending until the task runs


# ── signal tiering: signal 1 auto, signal 2 human-review ────────────────────
def test_signal1_attributed_failure_flags_human_review_false(tmp_path, monkeypatch):
    """An attributable signal-1 failure calls the human-review flag path with
    human_review=False (auto-rollback). We can't easily run real pytest here, so
    assert the flag call routes to auto-rollback, not to human_review."""
    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    calls = {"auto": 0, "human": 0}
    loop._auto_rollback = lambda *a, **k: calls.__setitem__("auto", calls["auto"] + 1)
    loop._flag_for_human = lambda *a, **k: calls.__setitem__("human", calls["human"] + 1)
    loop._resolve_flag("rid1", cr.FLAGGED, "signal_1 test regression attributable to x.py",
                       "snap1", "swarm_os/api/routes.py", human_review=False)
    assert calls["auto"] == 1
    assert calls["human"] == 0


def test_signal2_only_flags_human_review(tmp_path, monkeypatch):
    """Signal 2-only (graph-based, dynamic-import blind spot) -> HUMAN REVIEW,
    NEVER automatic. The flag path is called with human_review=True."""
    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    calls = {"auto": 0, "human": 0}
    loop._auto_rollback = lambda *a, **k: calls.__setitem__("auto", calls["auto"] + 1)
    loop._flag_for_human = lambda *a, **k: calls.__setitem__("human", calls["human"] + 1)
    loop._resolve_flag("rid2", cr.FLAGGED, "signal_2 downstream consumer breakage; HUMAN REVIEW",
                       "snap2", "swarm_os/api/routes.py", human_review=True)
    assert calls["auto"] == 0
    assert calls["human"] == 1


def test_flag_for_human_writes_surface_file(tmp_path):
    """A human-review flag must land in the dedicated human_review.jsonl the CLI
    /status reads — not just the audit trail."""
    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    loop._flag_for_human("swarm_os/api/routes.py", "rid9", "signal_2 downstream", "snap9")
    f = tmp_path / "human_review.jsonl"
    assert f.exists()
    content = f.read_text(encoding="utf-8")
    assert "rollback_human_review" in content
    assert "routes.py" in content


# ── unverifiable + never-reviewed expiry ────────────────────────────────────
def test_unverifiable_flags_for_human(tmp_path, monkeypatch):
    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    calls = {"human": 0}
    loop._flag_for_human = lambda *a, **k: calls.__setitem__("human", calls["human"] + 1)
    loop._resolve_unverifiable("rid3", "canary evaluation error: boom", "snap3", "swarm_os/api/routes.py")
    assert calls["human"] == 1


def test_expired_flags_bounded(tmp_path):
    """Never-reviewed flagged/unverifiable canaries expire after max_age_days so
    the registry + snapshots are bounded (the same cleanup discipline as
    checkpoints)."""
    ok, rid = cr.register_canary("swarm_os/api/routes.py", "snap1", window_minutes=5)
    # Force it to a flagged state with an old resolved_at.
    cr.resolve_canary(rid, cr.FLAGGED, "signal_2 HUMAN REVIEW")
    reg = cr.load_registry()
    reg[rid]["resolved_at"] = "2026-01-01T00:00:00+00:00"
    from runtime_v2.services.canary_registry import _save_registry
    _save_registry(reg)
    expired = cr.clear_expired_old_flags(max_age_days=14.0)
    assert expired == 1
    reg = cr.load_registry()
    assert reg[rid]["state"] == "expired"


def test_traceback_attribution(tmp_path):
    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    assert loop._traceback_attributes("  File \"swarm_os/api/routes.py\", line 12", "swarm_os/api/routes.py")
    assert not loop._traceback_attributes("  File \"other.py\", line 1", "swarm_os/api/routes.py")


def test_traceback_attribution_windows_separators(tmp_path):
    """Windows pytest output uses absolute backslash paths
    (C:\\...\\runtime_v2\\services\\indexer.py) while the canary stores the
    forward-slash relative path — the real `_run_related_tests` traceback shape
    (verbatim: only absolute Windows File lines, no dotted-module import frame).
    The matcher must normalize the output separators, not just the needle, or a
    syntax error whose traceback literally prints the repaired file's path gets
    mis-routed to HUMAN REVIEW instead of signal-1 auto-rollback (found live in
    the 2026 autonomy smoke test; unit tests only had forward-slash fixtures)."""
    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    real_tb = (
        'FFFFFFF                                                                  [100%]\n'
        '================================== FAILURES ===================================\n'
        'E     File "C:\\Users\\rober\\Projects\\v-horseshoe-v2\\runtime_v2\\services\\indexer.py", line 22\n'
        '        TOKEN_BUDGET_CHARS = 1800\n'
        '    IndentationError: unexpected indent\n'
        'C:\\Users\\rober\\Projects\\v-horseshoe-v2\\tests\\test_indexer_budget.py:54:   '
        'File "C:\\Users\\rober\\Projects\\v-horseshoe-v2\\runtime_v2\\services\\indexer.py", line 22\n'
        '=========================== short test summary info ===========================\n'
        'FAILED tests/test_indexer_budget.py::test_fit_token_budget_leaves_short_text_unchanged\n'
        '7 failed in 0.12s\n'
    )
    assert loop._traceback_attributes(real_tb, "runtime_v2/services/indexer.py")
    assert not loop._traceback_attributes(real_tb, "runtime_v2/services/watch_loop.py")
    assert not loop._traceback_attributes(real_tb, "other_file.py")
