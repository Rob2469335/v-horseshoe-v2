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

import json

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


def test_traceback_attribution_no_sibling_package_false_positive(tmp_path):
    """A failure in a SIBLING module of the same package must NOT be attributed
    to the repaired file. The old matcher matched the dotted PACKAGE prefix
    (e.g. `runtime_v2.services`), so an import frame
    `from runtime_v2.services import other` in other.py's traceback falsely
    attributed the failure to indexer.py — and since signal 1 is the
    authoritative auto-rollback trigger, that reverted the WRONG file. Only the
    repaired module's own path / dotted MODULE name may match."""
    loop = wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)
    # Sibling module fails; its traceback merely mentions the shared package.
    tb_sibling = (
        'E   File "C:\\Users\\rober\\Projects\\v-horseshoe-v2\\runtime_v2\\services\\other.py", line 5\n'
        '    from runtime_v2.services import helper\n'
        '    AssertionError\n'
    )
    assert not loop._traceback_attributes(tb_sibling, "runtime_v2/services/indexer.py")
    # Same package, different subpackage.
    tb_subpkg = 'E     File "C:\\Users\\rober\\Projects\\v-horseshoe-v2\\runtime_v2\\foo\\bar.py", line 1\n    Error\n'
    assert not loop._traceback_attributes(tb_subpkg, "runtime_v2/services/indexer.py")
    # The repaired module's OWN dotted name still matches (legit import frame).
    tb_own = 'E   File "<frozen>", line 1, in <module>\n    import runtime_v2.services.indexer\n'
    assert loop._traceback_attributes(tb_own, "runtime_v2/services/indexer.py")


# ── signal-2 downstream breakage (real implementation, not the stub) ────────
def _build_signal2_loop(tmp_path, monkeypatch):
    """WatchLoop with a tiny real project tree + its own event file, so the
    KnowledgeGraph build and the failure scan are REAL (not mocked)."""
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")
    # consumer.py imports core -> a static dependent of pkg.core.
    (tmp_path / "pkg" / "consumer.py").write_text(
        "from pkg import core\n\ndef run():\n    return core.x\n", encoding="utf-8"
    )
    monkeypatch.setattr(wl, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(wl, "_EVENTS_FILE", tmp_path / "events.jsonl")
    return wl.WatchLoop(SimpleNamespace(), interval_seconds=0.01)


def _write_failure_event(tmp_path, error_text: str):
    ev_file = tmp_path / "events.jsonl"
    ev_file.parent.mkdir(parents=True, exist_ok=True)
    with ev_file.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "event_type": "tool_result",
                    "payload": {
                        "result": {"ok": False, "error": error_text},
                    },
                }
            )
            + "\n"
        )


def test_signal2_detects_dependent_consumer_failure(tmp_path, monkeypatch):
    """Signal 2 must detect a recent failure naming a STATIC dependent of the
    repaired file (the whole point of the graph-based downstream check)."""
    loop = _build_signal2_loop(tmp_path, monkeypatch)
    _write_failure_event(
        tmp_path,
        'E   File "C:/repo/pkg/consumer.py", line 3, in run\n    return core.x\n    AttributeError',
    )
    assert loop._signal2_downstream_breakage("pkg/core.py") is True


def test_signal2_no_false_positive_for_unrelated_failure(tmp_path, monkeypatch):
    """A failure naming an unrelated module must NOT trigger signal 2."""
    loop = _build_signal2_loop(tmp_path, monkeypatch)
    _write_failure_event(
        tmp_path,
        'E   File "C:/repo/other/module.py", line 1\n    NameError',
    )
    assert loop._signal2_downstream_breakage("pkg/core.py") is False


def test_signal2_fails_open_when_no_dependents(tmp_path, monkeypatch):
    """A repaired file with no static dependents (or not in the graph) cannot
    flag signal 2 — it returns False (fail-open), never raises."""
    loop = _build_signal2_loop(tmp_path, monkeypatch)
    _write_failure_event(tmp_path, "some failure text")
    # Leaf module with no importers -> no dependents -> False.
    assert loop._signal2_downstream_breakage("pkg/consumer.py") is False
    # Unknown module -> False without raising.
    assert loop._signal2_downstream_breakage("pkg/ghost.py") is False


def test_signal2_ignores_ok_events(tmp_path, monkeypatch):
    """Only ok:False tool_result events count — a passing event must not
    trigger signal 2."""
    loop = _build_signal2_loop(tmp_path, monkeypatch)
    ev_file = tmp_path / "events.jsonl"
    ev_file.parent.mkdir(parents=True, exist_ok=True)
    with ev_file.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "event_type": "tool_result",
                    "payload": {
                        "result": {"ok": True, "error": "pkg.consumer fine"},
                    },
                }
            )
            + "\n"
        )
    assert loop._signal2_downstream_breakage("pkg/core.py") is False


# ── soft-case signal 3 (elevated downstream failure rate) ────────────────────
def test_soft_case_single_hit_below_threshold(tmp_path, monkeypatch):
    """A single failure naming a dependent is signal 2's job, NOT signal 3 —
    the soft-case rate trigger needs a PATTERN (default threshold 2)."""
    loop = _build_signal2_loop(tmp_path, monkeypatch)
    _write_failure_event(
        tmp_path,
        'E   File "C:/repo/pkg/consumer.py", line 3, in run\n    AttributeError',
    )
    assert loop._soft_case_elevated_failure_rate("pkg/core.py") is False


def test_soft_case_elevated_rate_flags(tmp_path, monkeypatch):
    """Multiple recent failures naming the repaired module's dependents must
    trip the soft-case rate trigger (human-review tier)."""
    loop = _build_signal2_loop(tmp_path, monkeypatch)
    for i in range(2):
        _write_failure_event(
            tmp_path,
            f'E   File "C:/repo/pkg/consumer.py", line {i}, in run\n    AttributeError #{i}',
        )
    assert loop._soft_case_elevated_failure_rate("pkg/core.py") is True


def test_soft_case_ignores_unrelated_failures(tmp_path, monkeypatch):
    """Failures naming unrelated modules must NOT trip the soft-case trigger."""
    loop = _build_signal2_loop(tmp_path, monkeypatch)
    for i in range(5):
        _write_failure_event(
            tmp_path,
            f'E   File "C:/repo/other/mod{i}.py", line 1\n    Error',
        )
    assert loop._soft_case_elevated_failure_rate("pkg/core.py") is False


def test_soft_case_wired_into_evaluate_as_human_review(tmp_path, monkeypatch):
    """In the no-related-tests path, an elevated downstream failure rate must
    flag for HUMAN review (never auto-rollback)."""
    loop = _build_signal2_loop(tmp_path, monkeypatch)
    for i in range(2):
        _write_failure_event(
            tmp_path,
            f'E   File "C:/repo/pkg/consumer.py", line {i}\n    AttributeError',
        )
    calls = {"auto": 0, "human": 0}
    loop._auto_rollback = lambda *a, **k: calls.__setitem__("auto", calls["auto"] + 1)
    loop._flag_for_human = lambda *a, **k: calls.__setitem__("human", calls["human"] + 1)
    loop._resolve_flag(
        "rid3",
        cr.FLAGGED,
        "signal_3 elevated downstream failure rate; HUMAN REVIEW",
        "snap3",
        "pkg/core.py",
        human_review=True,
    )
    assert calls["auto"] == 0
    assert calls["human"] == 1


# ── canary eval consumes the REAL structured dict from _run_related_tests ────
def test_canary_eval_signal1_uses_real_dict_shape(tmp_path, monkeypatch):
    """_run_related_tests returns a STRUCTURED DICT
    {ok, output, flaky, initial_result, retry_result}. The canary evaluator must
    consume that dict. The old `ok, output = result` unpacked the dict KEYS
    (5 into 2) -> ValueError -> the generic handler resolved every tested canary
    to 'unverifiable', so the authoritative signal-1 auto-rollback NEVER fired.
    This drives the evaluator with the real shape and asserts signal-1 routes to
    automatic rollback (human_review=False)."""
    import asyncio
    import organism_console.core.repair_engine as re_mod

    loop = _build_signal2_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(
        re_mod,
        "_run_related_tests",
        lambda fp: {
            "ok": False,
            "output": (
                'E   File "C:/repo/runtime_v2/services/indexer.py", line 22\n'
                "    IndentationError: unexpected indent"
            ),
            "flaky": False,
            "initial_result": "fail",
            "retry_result": "fail",
        },
    )
    calls = {"auto": 0, "human": 0}
    loop._auto_rollback = lambda *a, **k: calls.__setitem__("auto", calls["auto"] + 1)
    loop._flag_for_human = lambda *a, **k: calls.__setitem__("human", calls["human"] + 1)
    loop._resolve_flag = lambda rid, st, det, sid, f, human_review: calls.__setitem__(
        "auto" if not human_review else "human",
        calls["auto" if not human_review else "human"] + 1,
    )

    asyncio.run(
        loop._evaluate_canary(
            {
                "repair_id": "ridX",
                "file": "runtime_v2/services/indexer.py",
                "snapshot_id": "snapX",
            }
        )
    )
    # signal-1 attributable failure -> automatic rollback (auto=1), NOT human review.
    assert calls["auto"] == 1
    assert calls["human"] == 0
