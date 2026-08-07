"""Tests for the autonomous-repair constitutional guards (path allowlist,
anti-truncation, circuit breaker, and related-test discovery)."""
from pathlib import Path

import organism_console.core.repair_engine as repair_engine


def test_is_repairable_path_allows_source_trees():
    for p in ("src/core/foo.py", "swarm_os/memory/memory_bridge.py", "runtime_v2/services/x.py"):
        assert repair_engine._is_repairable_path(Path(p))


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
