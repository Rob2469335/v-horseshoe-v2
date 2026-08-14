"""Tests for the autonomous-repair constitutional guards (path allowlist,
anti-truncation, circuit breaker, and related-test discovery)."""
from pathlib import Path

import organism_console.core.repair_engine as repair_engine


def test_is_repairable_path_allows_source_trees():
    # App-code source trees are repairable (src/ was deleted 2026-08; not listed).
    for p in ("swarm_os/services/tool_registry.py", "runtime_v2/services/x.py",
              "organism_console/core/y.py"):
        assert repair_engine._is_repairable_path(Path(p)), p


def test_is_repairable_path_blocks_self_modify_machinery():
    # The autonomy policy (autonomy_policy.json) blocks the self-healing machinery
    # AND its dependencies (dependency-aware scan) from unsupervised repair —
    # recovery_engine imports memory_bridge, which imports vector_store, so all
    # three are off-limits to a buggy repair.
    blocked_self = (
        "swarm_os/healing/recovery_engine.py",
        "swarm_os/healing/governor.py",
        "swarm_os/services/reflection_loop.py",
        "swarm_os/services/security_gate.py",
        "swarm_os/memory/memory_bridge.py",
        "swarm_os/services/vector_store.py",
        "swarm_os/app/main.py",
        "autonomy_policy.json",
    )
    for p in blocked_self:
        assert not repair_engine._is_repairable_path(Path(p)), p


def test_is_repairable_path_blocks_sensitive():
    blocked = (
        "tests/test_foo.py",
        "config/settings.py",
        ".env",
        "AGENTS.md",
        "package.json",
        "models/qwen.gguf",
        "swarm_os/healing/distilled_cures.json",
        "swarm_os/healing/generated_tests/test_x.py",
    )
    for p in blocked:
        assert not repair_engine._is_repairable_path(Path(p)), p


def test_is_repairable_path_blocks_non_py():
    assert not repair_engine._is_repairable_path(Path("swarm_os/api/routes.ts"))


def test_anti_truncation_rejects_shrink():
    original = "x" * 1000
    assert repair_engine._anti_truncation_ok(original, "y" * 850)
    assert not repair_engine._anti_truncation_ok(original, "y" * 100)


def test_anti_truncation_bypasses_tiny_files():
    assert repair_engine._anti_truncation_ok("x" * 50, "y")


def test_circuit_breaker_trips_after_three_failures(tmp_path, monkeypatch):
    breaker_file = tmp_path / "repair_breaker.json"
    monkeypatch.setattr(repair_engine, "BREAKER_FILE", breaker_file)
    monkeypatch.setattr(repair_engine, "date", _FakeDate)
    repair_engine._save_breaker({
        "date": _FakeDate.today_iso,
        "repairs": 0,
        "consecutive_failures": 0,
        "open_until": 0.0,
    })

    for _ in range(3):
        allowed, reason = repair_engine._circuit_allows_repair()
        assert allowed, reason
        repair_engine._record_repair_result(False)

    allowed, reason = repair_engine._circuit_allows_repair()
    assert not allowed
    assert "circuit open" in reason


def test_circuit_breaker_daily_cap(tmp_path, monkeypatch):
    breaker_file = tmp_path / "repair_breaker.json"
    monkeypatch.setattr(repair_engine, "BREAKER_FILE", breaker_file)
    monkeypatch.setattr(repair_engine, "MAX_DAILY_REPAIRS", 2)
    monkeypatch.setattr(repair_engine, "date", _FakeDate)
    repair_engine._save_breaker({
        "date": _FakeDate.today_iso,
        "repairs": 0,
        "consecutive_failures": 0,
        "open_until": 0.0,
    })
    assert repair_engine._circuit_allows_repair()[0]
    repair_engine._record_repair_result(True)
    assert repair_engine._circuit_allows_repair()[0]
    repair_engine._record_repair_result(True)
    allowed, reason = repair_engine._circuit_allows_repair()
    assert not allowed
    assert "daily repair cap" in reason


def test_handle_event_line_null_payloads_no_crash():
    """Regression: RepairWatchman's parse loop crashed with 'NoneType' object is
    not subscriptable on event lines whose payload/result/arguments are None.
    The extracted _handle_event_line helper must be None-tolerant and never
    raise (unexpected shapes are skipped)."""
    events = [
        {"event_type": "tool_result", "payload": None},
        {"event_type": "tool_result", "payload": {"result": None}},
        {"event_type": "tool_result", "payload": {"result": {"ok": False, "error": None}}},
        {"event_type": "turn_budget_exhausted", "payload": None},
        {"event_type": "turn_budget_exhausted", "payload": {"agent_id": None, "prompt": None}},
        {"event_type": "agent_action", "payload": {"action": "final", "turn": 1}},
        "not-a-dict",
        None,
    ]
    for d in events:
        repair_engine._handle_event_line(None, d)
    # Also: a real failing tool_result should NOT crash and should attempt a repair.
    class FakeEngine:
        def __init__(self):
            self.calls = []
        def repair(self, error_text, file_path=None):
            self.calls.append(("repair", error_text, file_path))
        def diagnose_and_repair(self, error_text, file_path=None):
            self.calls.append(("diagnose", error_text, file_path))

    eng = FakeEngine()
    repair_engine._handle_event_line(eng, {
        "event_type": "tool_result",
        "payload": {"result": {"ok": False, "error": "File not found: runtime_v2/services/agent_service.py"}},
    })
    assert eng.calls, "engine should have been invoked for a real failure"
    assert "agent_service.py" in eng.calls[0][1]
    # Null-payload tool_result must NOT invoke the engine (nothing to repair).
    eng.calls.clear()
    repair_engine._handle_event_line(eng, {"event_type": "tool_result", "payload": None})
    assert not eng.calls


def test_watchman_invokes_handle_event_line(tmp_path, monkeypatch):
    """The threaded _watch must route parsed lines through the extracted helper
    (so the null-payload fix actually applies at runtime)."""
    # Can't easily create the real path; instead verify the method wiring via
    # the module: _watch calls _handle_event_line(self.engine, data).
    import inspect
    src = inspect.getsource(repair_engine.RepairWatchman._watch)
    assert "_handle_event_line(self.engine, data)" in src


def test_related_test_discovery_finds_governor():
    tests = repair_engine._find_related_tests(Path("swarm_os/healing/governor.py"))
    names = [t.name for t in tests]
    assert any("governor" in n for n in names)


class _FakeProc:
    def __init__(self, code, out="output"):
        self.returncode = code
        self.stdout = out
        self.stderr = ""


def test_run_related_tests_treats_flaky_pass_as_sound(monkeypatch):
    """A first-run failure that passes on --lf re-run is a FLAKY test, not a
    broken repair — the repair must be treated as sound (True) so a good fix is
    not rolled back on a transient failure."""
    import subprocess as _sp
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return _FakeProc(1, "FAILED pytest: 1 failed")
        return _FakeProc(0, "1 passed")

    monkeypatch.setattr(_sp, "run", fake_run)
    monkeypatch.setattr(repair_engine, "_find_related_tests",
                        lambda path: [Path("tests/test_x.py")])
    ok, out = repair_engine._run_related_tests(Path("swarm_os/services/x.py"))
    assert ok is True, f"flaky re-run must count as sound: {out}"
    assert "[flaky]" in out
    assert len(calls) == 2  # original + last-failed re-run
    assert "--lf" in calls[1]


def test_run_related_tests_flaky_rerun_still_failing_is_broken(monkeypatch):
    """If the --lf re-run ALSO fails, the repair is genuinely broken (False) —
    a flake retry must never mask a real regression."""
    import subprocess as _sp
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeProc(1, "FAILED still failing")

    monkeypatch.setattr(_sp, "run", fake_run)
    monkeypatch.setattr(repair_engine, "_find_related_tests",
                        lambda path: [Path("tests/test_x.py")])
    ok, out = repair_engine._run_related_tests(Path("swarm_os/services/x.py"))
    assert ok is False, "repeated failure must remain broken"
    assert "last-failed re-run" in out


class _FakeDate:
    today_iso = "2099-01-01"

    @classmethod
    def today(cls):
        return cls()

    def isoformat(self):
        return self.today_iso

def test_classify_fix_class_prompt_sensitivity_default():
    # Explicit rule/format violations -> prompt_sensitivity (cheap to patch)
    assert repair_engine.classify_fix_class("Output was malformed JSON: expected, got garbage")
    assert repair_engine.classify_fix_class("syntax error at line 3: unexpected indent") == "prompt_sensitivity"


def test_classify_fix_class_real_tracebacks_are_patchable():
    # 2026 L2 regression: REAL Python tracebacks (including ones the user has
    # actually hit) must classify as prompt_sensitivity so the T2 patch path
    # still fires — these are ordinary code defects, NOT model limitations.
    patchable_via_ps_term = [
        "TypeError: cannot unpack non-iterable NoneType object",
        "ImportError: cannot import name 'X' from 'Y'",
        "ValueError: cannot convert float NaN to integer",
        "TypeError: unable to serialize object of type 'NoneType'",
        "NameError: name 'baseline_passed' is not defined",
        "AttributeError: 'NoneType' object has no attribute 'check'",
        "KeyError: 'payload'",
        "IndexError: list index out of range",
    ]
    for tb in patchable_via_ps_term:
        assert repair_engine.classify_fix_class(tb) == "prompt_sensitivity", tb


def test_ps_term_explicitly_hits_not_defined_and_nameerror():
    # This case actually exercises a PS-term match ("not defined" + "nameerror"),
    # NOT the default — guards against someone later narrowing the default and
    # this silently changing meaning.
    tb = "NameError: name 'baseline_passed' is not defined"
    assert repair_engine.classify_fix_class(tb) == "prompt_sensitivity"
    lower = tb.lower()
    assert any(t in lower for t in repair_engine.FIX_PS_TERMS)


def test_unmatched_traceback_falls_through_to_patchable_default():
    # "coroutine ... was never awaited" matches NO PS term and NO MV term — this
    # verifies unmatched text falls through to the patchable DEFAULT without
    # accidentally tripping MV, rather than pretending it hit a PS signal.
    tb = "coroutine 'FailureDetector.check' was never awaited"
    assert repair_engine.classify_fix_class(tb) == "prompt_sensitivity"
    lower = tb.lower()
    assert not any(t in lower for t in repair_engine.FIX_PS_TERMS)
    assert not any(t in lower for t in repair_engine.FIX_MV_TERMS)


def test_classify_fix_class_model_variability_only_without_structural_signal():
    # Genuinely model-limitation language, with NO structural/schema signal,
    # -> model_variability (skip LLM patch).
    assert repair_engine.classify_fix_class("The model hallucinated and gave a wrong answer") == "model_variability"
    assert repair_engine.classify_fix_class("I don't know how to do this, it's out of scope") == "model_variability"


def test_should_attempt_llm_patch_skips_mv():
    assert repair_engine._should_attempt_llm_patch("malformed json: expected value")
    assert not repair_engine._should_attempt_llm_patch("hallucinated answer")

def test_tier2_skips_llm_patch_for_model_variability(tmp_path, monkeypatch):
    # Diagnose-before-patch: an MV failure must NOT make the /generate call even
    # when the T2 branch is reachable (cmd_ctx present). Isolate the circuit
    # breaker so a prior test's breaker state can't short-circuit this into the
    # early-return path.
    monkeypatch.setattr(repair_engine, "BREAKER_FILE", tmp_path / "breaker.json")
    called = {"n": 0}
    def fake_call(*a, **k):
        called["n"] += 1
        raise AssertionError("should not call LLM for model_variability")
    monkeypatch.setattr("organism_console.api_client.call_api", fake_call)

    class _FakeCtx:
        def __init__(self):
            self.console = type("C", (), {"print": staticmethod(lambda *a, **k: None)})()
            self.state = type("S", (), {"active_agent": "coder"})()
    orch = repair_engine.TieredRepairOrchestrator(cmd_ctx=_FakeCtx())
    f = tmp_path / "bug.py"
    f.write_text("x = 1\n", encoding="utf-8")
    res = orch.repair("The model gave a wrong answer and it's out of scope", file_path=f)
    assert res["fix_class"] == "model_variability"
    assert not res["fixed"]
    assert "model_variability" in (res.get("validation_error") or "")
    assert called["n"] == 0
    # Disclosure: this path skips the patch but does NOT re-dispatch a retry
    # (regeneration is the agent loop's job upstream). Make that explicit.
    assert res.get("retry_dispatched") is False

def test_save_breaker_uses_filelock(tmp_path, monkeypatch):
    """2026 coexistence: the shared breaker file must be written under filelock so
    two engines can never lose an increment or race the trip threshold — the
    write path is never the weak link."""
    import inspect
    src = inspect.getsource(repair_engine._save_breaker)
    assert "FileLock" in src
    assert "BREAKER_FILE" in src


def test_repair_security_gate_reverts_banned_construct(tmp_path, monkeypatch):
    """Independent verify (P0): a repaired file that INTRODUCES a banned
    construct (subprocess/exec/eval) must be reverted, not shipped. The
    pre-repair file passed the gate, so this is the 'security signals not worse
    than baseline' condition — a repair that adds a banned import is a
    security regression."""
    # Isolate the security gate: the path guard runs before it, and tmp_path is
    # outside the repo allowlist — bypass only the path check for this unit test.
    monkeypatch.setattr(repair_engine, "_is_repairable_path", lambda p: True)
    orch = repair_engine.TieredRepairOrchestrator(cmd_ctx=None)
    f = tmp_path / "bug.py"
    original = "x = 1\n"
    f.write_text(original, encoding="utf-8")
    result = {"fixed": True}
    # Repaired content smuggles a banned module import.
    f.write_text("import subprocess\nsubprocess.call('x')\n", encoding="utf-8")
    ok = orch._snapshot_and_validate(f, result, original)
    assert ok is False, "repair introducing a banned construct must be reverted"
    assert result["fixed"] is False
    assert "security gate" in (result.get("validation_error") or "").lower()
    assert f.read_text(encoding="utf-8") == original, "file must be reverted to original"
    # A clean repair still passes the gate.
    f.write_text("def helper(x):\n    return x + 1\n", encoding="utf-8")
    result2 = {"fixed": True}
    ok2 = orch._snapshot_and_validate(f, result2, original)
    assert ok2 is True, "clean repair must pass the security gate"



