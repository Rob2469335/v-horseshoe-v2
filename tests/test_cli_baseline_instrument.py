"""Phase-1 instrument: order-preserving tool parse, failure taxonomy, attempts.

The read-only baseline is the ruler for the whole learning program, so the
instrument itself is pinned here: tool ORDER is a signal (the old
`sorted(set(...))` destroyed it), the failure category is deterministic (no
LLM), attempt 1 is always the recorded datum, and `record=False` performs no
learning write (the baseline must not change what it measures).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MOD = Path(__file__).resolve().parents[1] / "qwen_train" / "run_curriculum.py"
_spec = importlib.util.spec_from_file_location("run_curriculum_instr", _MOD)
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)


# --------------------------------------------------------------------------
# Order preservation — the signal the old parser threw away
# --------------------------------------------------------------------------


def test_parse_tools_used_preserves_call_order():
    out = rc.parse_tools_used("⚡ sandbox_repl\n⚡ filesystem\n⚡ web_search")
    assert out == ["sandbox_repl", "filesystem", "web_search"]
    assert out != sorted(out)  # sorted(set(...)) would have reordered this


def test_parse_tools_used_dedupes_keeping_first_occurrence():
    assert rc.parse_tools_used("⚡ a\n⚡ b\n⚡ a") == ["a", "b"]


def test_parse_tools_succeeded_preserves_order():
    assert rc.parse_tools_succeeded("✓ z\n✓ a") == ["z", "a"]


def test_dedupe_helper_empty():
    assert rc._dedupe_in_order([]) == []


# --------------------------------------------------------------------------
# Deterministic failure taxonomy
# --------------------------------------------------------------------------


def test_classify_passed():
    assert rc.classify_failure({"verified": True}) == "passed"


def test_classify_ineligible_wins_over_other_signals():
    res = {"verified": False, "ineligible": True, "cli_ok": False}
    assert rc.classify_failure(res) == "ineligible"


def test_classify_harness_violation():
    res = {
        "verified": False,
        "cli_ok": True,
        "verify_reason": "check.py modified (forbidden)",
    }
    assert rc.classify_failure(res) == "harness_violation"


def test_classify_timeout():
    res = {"verified": False, "cli_ok": False, "timed_out": True}
    assert rc.classify_failure(res) == "timeout"


def test_classify_cli_error():
    assert rc.classify_failure({"verified": False, "cli_ok": False}) == "cli_error"


def test_classify_no_change():
    res = {"verified": False, "cli_ok": True}
    assert rc.classify_failure(res, module_changed=False) == "no_change"


def test_classify_parse_error_beats_test_failure():
    res = {"verified": False, "cli_ok": True}
    got = rc.classify_failure(
        res, module_changed=True, check_output="SyntaxError: invalid syntax"
    )
    assert got == "parse_error"


def test_classify_test_failure():
    res = {"verified": False, "cli_ok": True}
    assert rc.classify_failure(res, module_changed=True) == "test_failure"


def test_classify_unknown_never_guesses():
    assert rc.classify_failure({"verified": False, "cli_ok": True}) == "unknown"


def test_taxonomy_contains_every_returned_category():
    for cat in (
        "passed",
        "ineligible",
        "harness_violation",
        "timeout",
        "cli_error",
        "no_change",
        "parse_error",
        "test_failure",
        "unknown",
    ):
        assert cat in rc.FAILURE_CATEGORIES


# --------------------------------------------------------------------------
# Attempts / recovery — attempt 1 is the datum
# --------------------------------------------------------------------------


def test_attempt_one_is_the_recorded_baseline_datum(monkeypatch):
    seq = iter(
        [
            {"verified": False, "cli_ok": True, "verify_reason": "assert failed"},
            {"verified": True, "cli_ok": True, "verify_reason": "ok"},
        ]
    )
    monkeypatch.setattr(rc, "_attempt_once", lambda item, t, a, rec: dict(next(seq)))

    res = rc.run_item({"id": "t"}, attempts=2)

    assert res["first_attempt_success"] is False  # attempt 1 retained
    assert res["attempts_to_success"] == 2
    assert res["recovered"] is True
    assert res["last_verified"] is True


def test_reset_runs_before_each_retry_only(monkeypatch):
    attempts: list[int] = []
    monkeypatch.setattr(
        rc,
        "_attempt_once",
        lambda item, t, a, rec: attempts.append(1) or {"verified": False, "cli_ok": True},
    )
    resets: list[int] = []
    rc.run_item({"id": "t"}, attempts=3, reset=lambda: resets.append(1))

    assert len(attempts) == 3
    assert len(resets) == 2  # before attempts 2 and 3, never before attempt 1


def test_timeout_stops_retrying(monkeypatch):
    attempts: list[int] = []
    monkeypatch.setattr(
        rc,
        "_attempt_once",
        lambda item, t, a, rec: attempts.append(1)
        or {"verified": False, "cli_ok": False, "timed_out": True},
    )
    res = rc.run_item({"id": "t"}, attempts=5)

    assert len(attempts) == 1  # a timeout is not cured by retrying
    assert res["failure_category"] == "timeout"


# --------------------------------------------------------------------------
# Read-only mode — measuring must not change the thing measured
# --------------------------------------------------------------------------


class _FakeProc:
    """Stand-in for the Popen capture pattern (Popen + communicate).

    Updated 2026-09-15 with the capture fix: `_attempt_once` no longer uses
    `subprocess.run` (whose TimeoutExpired carries no partial output), so the
    mock must model what it uses now. The assertions below are unchanged.
    """

    returncode = 0

    def __init__(self, *args, **kwargs):
        self.stdout = '⚡ filesystem\n{"ok": true, "content": "done"}'
        self.stderr = ""

    def communicate(self, input=None, timeout=None):  # noqa: A002
        return self.stdout, self.stderr

    def kill(self):
        pass


def test_record_false_performs_no_learning_write(monkeypatch):
    import runtime_v2.services.tool_policy as tp

    calls: list[int] = []
    monkeypatch.setattr(tp, "record_observation", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(rc.subprocess, "Popen", lambda *a, **k: _FakeProc())

    item = {
        "id": "t",
        "prompt": "p",
        "target_tools": ["filesystem"],
        "verify": {"type": "contains", "mode": "any", "value": ["done"]},
    }

    rc._attempt_once(item, 5, False, record=False)
    assert calls == []  # read-only: nothing written

    rc._attempt_once(item, 5, False, record=True)
    assert calls == [1]  # normal path still writes
