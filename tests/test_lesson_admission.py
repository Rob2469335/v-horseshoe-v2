"""Tests for the deterministic lesson admissibility gate (swarm_os/services/lesson_admission.py).

Pins the rule-admissibility + verify-then-admit + abstention behaviour that stops
stale/wrong lessons from shaping decisions (reflexion rot / self-evaluator drift).
"""

from swarm_os.services import lesson_admission as la

NOW = 1_000_000.0


def _cand(**kw):
    base = {
        "lesson_id": "L1",
        "component": "coder",
        "scope": "agent",
        "confidence": 0.8,
        "success_count": 2,
        "ts": NOW,
        "warned_tools": ["filesystem"],
    }
    base.update(kw)
    return base


def _ctx(**kw):
    base = {"agent_id": "coder", "tools": ["filesystem", "sandbox_repl"]}
    base.update(kw)
    return base


def test_decayed_confidence_fresh_vs_old():
    assert abs(la.decayed_confidence(_cand(), NOW, 30) - 0.8) < 1e-6
    assert la.decayed_confidence(_cand(ts=NOW - 400 * 86400), NOW, 30) < 0.01
    # no timestamp -> no decay
    assert la.decayed_confidence(_cand(ts=None), NOW, 30) == 0.8


def test_admit_good_candidate():
    v = la.admit(_cand(), _ctx(), NOW)
    assert v["admitted"] is True
    assert v["reasons"] == []


def test_reject_component_mismatch():
    v = la.admit(_cand(component="researcher"), _ctx(agent_id="coder"), NOW)
    assert v["admitted"] is False
    assert any("component" in r for r in v["reasons"])


def test_shared_scope_bypasses_component_mismatch():
    v = la.admit(_cand(component="researcher", scope="shared"), _ctx(agent_id="coder"), NOW)
    assert v["admitted"] is True


def test_reject_tool_not_in_play():
    v = la.admit(_cand(warned_tools=["playwright"]), _ctx(tools=["filesystem"]), NOW)
    assert v["admitted"] is False
    assert any("warned_tools" in r for r in v["reasons"])


def test_reject_low_confidence_and_no_evidence():
    assert la.admit(_cand(confidence=0.2), _ctx(), NOW)["admitted"] is False
    assert la.admit(_cand(success_count=0), _ctx(), NOW)["admitted"] is False


def test_reject_stale_lesson():
    v = la.admit(_cand(ts=NOW - 400 * 86400), _ctx(), NOW)
    assert v["admitted"] is False
    assert any("decayed" in r for r in v["reasons"])


def test_admit_all_abstains_when_none_pass():
    out = la.admit_all([_cand(confidence=0.1)], _ctx(), NOW)
    assert out["abstained"] is True
    assert out["admitted"] == []


def test_admit_all_top_k_limits():
    cands = [_cand(lesson_id=f"L{i}", confidence=0.9 - i * 0.01) for i in range(6)]
    out = la.admit_all(cands, _ctx(), NOW, top_k=2)
    assert len(out["admitted"]) == 2


def test_admit_all_conflict_drops_lower_evidence():
    strong = _cand(lesson_id="strong", confidence=0.9, conflicts_with=["weak"])
    weak = _cand(lesson_id="weak", confidence=0.7)
    out = la.admit_all([strong, weak], _ctx(), NOW)
    assert [c["lesson_id"] for c in out["admitted"]] == ["strong"]
    assert "weak" in out["dropped_conflicts"]
