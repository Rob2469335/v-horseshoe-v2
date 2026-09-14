"""Tests for the gold-set miner / diversity filter (qwen_train/mine_gold.py).

Pins the pipeline invariants: outcome classification, exact-dupe collapse (most
efficient verified run wins), per-shape diversity cap, heuristic critical-step
detection, and GOLD tiering (recovery > critical > success; failures/loops never
enter gold). RAW trajectories are never written by the filter.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import mine_gold as mg  # noqa: E402


def _rec(run_id="r1", **kw):
    base = {
        "run_id": run_id,
        "outcome": "SUCCESS",
        "succeeded": True,
        "n_steps": 3,
        "tool_seq": ["filesystem", "filesystem", "sandbox_repl"],
        "labels": ["NORMAL_SUCCESS", "NORMAL_SUCCESS", "NORMAL_SUCCESS"],
        "shape": "filesystem>filesystem>sandbox_repl",
        "sig": "a|b|c",
        "n_ineligible": 0,
        "n_failure": 0,
        "recovery": False,
        "loop": False,
    }
    base.update(kw)
    return base


# ── classification ───────────────────────────────────────────────────────────
def test_classify_success_requires_verifier_not_false():
    assert mg.classify(_rec(), 0) == "SUCCESS"
    assert mg.classify(_rec(run_verified=False), 0) == "FAILURE"


def test_classify_ineligible_and_environment_and_failure():
    assert mg.classify(_rec(ineligible=True), 0) == "INELIGIBLE"
    assert mg.classify(_rec(succeeded=False, n_ineligible=1), 0) == "INELIGIBLE"
    assert mg.classify(_rec(succeeded=False), 2) == "ENVIRONMENT"
    assert mg.classify(_rec(succeeded=False), 0) == "FAILURE"


# ── dedupe ───────────────────────────────────────────────────────────────────
def test_dedupe_collapses_exact_and_keeps_efficient():
    slow = _rec("slow", n_steps=5)
    fast = _rec("fast", n_steps=3)
    kept, removed = mg.dedupe([slow, fast])
    assert [r["run_id"] for r in kept] == ["fast"]
    assert removed == ["slow"]


def test_dedupe_keeps_different_shapes():
    a = _rec("a", shape="filesystem", sig="x")
    b = _rec("b", shape="filesystem>sandbox_repl", sig="x")
    kept, removed = mg.dedupe([a, b])
    assert {r["run_id"] for r in kept} == {"a", "b"}
    assert removed == []


def test_dedupe_prefers_verified_over_unverified_same_cost():
    bad = _rec("bad", run_verified=False)
    good = _rec("good", run_verified=True)
    kept, _ = mg.dedupe([bad, good])
    assert kept[0]["run_id"] == "good"


# ── diversity cap ────────────────────────────────────────────────────────────
def test_cap_diversity_limits_per_shape():
    recs = [_rec(f"r{i}", sig=f"s{i}") for i in range(5)]  # same shape, diff sig
    kept, dropped = mg.cap_diversity(recs, per_shape=2)
    assert len(kept) == 2
    assert len(dropped) == 3


# ── critical steps ───────────────────────────────────────────────────────────
def test_critical_steps_finds_recovery_and_final_success():
    r = _rec(
        tool_seq=["filesystem", "sandbox_repl", "filesystem"],
        labels=["FAILURE", "NORMAL_SUCCESS", "NORMAL_SUCCESS"],
    )
    assert mg.critical_steps(r) == [1, 2]


def test_critical_steps_none_when_no_success():
    r = _rec(tool_seq=["filesystem"], labels=["FAILURE"])
    assert mg.critical_steps(r) == []


# ── gold selection ───────────────────────────────────────────────────────────
def test_select_gold_excludes_failures_and_loops():
    ok = _rec("ok")
    fail = _rec("fail", outcome="FAILURE", succeeded=False)
    loop = _rec("loop", loop=True)
    gold = mg.select_gold([ok, fail, loop])
    assert [g["run_id"] for g in gold] == ["ok"]


def test_select_gold_tiers_recovery_first():
    rec = _rec("rec", recovery=True)
    crit = _rec("crit", recovery=False)  # 2 distinct tools -> real choice
    plain = _rec(
        "plain",
        recovery=False,
        tool_seq=["filesystem"],
        labels=["NORMAL_SUCCESS"],
        n_steps=1,
    )
    gold = mg.select_gold([plain, crit, rec])
    assert [g["tier"] for g in gold] == ["recovery", "critical", "success"]


def test_tools_helped_rate():
    recs = [
        _rec("a", tool_seq=["filesystem", "filesystem"], labels=["NORMAL_SUCCESS", "FAILURE"]),
    ]
    d = mg._tools_helped(recs)
    assert d["filesystem"] == {"used": 2, "success": 1, "rate": 0.5}


def _timed(run_id, i, **kw):
    r = _rec(run_id, **kw)
    r["_mtime"] = float(i)
    return r


def test_learning_curve_buckets():
    recs = [_timed(f"r{i}", i) for i in range(10)]
    curve = mg.learning_curve(recs, buckets=5)
    assert curve["n"] == 10
    assert curve["buckets"][0]["experience_band"].startswith("1-")
    assert len(curve["buckets"]) == 5


def test_self_healing_rate():
    healed = _rec("h", labels=["FAILURE", "NORMAL_SUCCESS"], outcome="SUCCESS")
    not_healed = _rec("n", labels=["FAILURE"], outcome="FAILURE")
    clean = _rec("c", labels=["NORMAL_SUCCESS"], outcome="SUCCESS")
    sh = mg.self_healing([healed, not_healed, clean])
    assert sh["runs_with_a_failure"] == 2
    assert sh["became_verified_success"] == 1
    assert sh["self_healing_rate"] == 0.5


def test_stratified_trend_same_mix_agrees():
    recs = [
        _timed("a", 0, shape="A"),
        _timed("b", 1, shape="A"),
        _timed("c", 2, shape="A"),
        _timed("d", 3, shape="A"),
    ]
    st = mg.stratified_trend(recs)
    assert st["raw_delta"] == st["mix_adjusted_delta"]


def test_stratified_trend_exposes_mix_confound():
    # early: only shape A (all success). late: A success + B failure.
    # A shape-change (mix) inflates the raw drop beyond the within-shape effect.
    recs = [
        _timed("e1", 0, shape="A"),
        _timed("e2", 1, shape="A"),
        _timed("l1", 2, shape="A"),
        _timed("l2", 3, shape="B", outcome="FAILURE", succeeded=False),
    ]
    st = mg.stratified_trend(recs)
    assert st["raw_delta"] != st["mix_adjusted_delta"]


# ── tip types + provenance (arXiv:2603.10600 / ASG-SI) ───────────────────────
def test_tip_type_recovery_strategy_optimization():
    assert mg.tip_type(_rec("r", recovery=True), 3) == "recovery"
    assert mg.tip_type(_rec("s", n_steps=3), 3) == "strategy"
    assert mg.tip_type(_rec("o", n_steps=10), 3) == "optimization"


def test_tip_type_no_median_is_strategy():
    assert mg.tip_type(_rec("s", n_steps=9), 0) == "strategy"


def test_select_gold_carries_tip_type_and_provenance():
    rec = _rec(
        "g",
        recovery=True,
        state_hashes=["abc", "def"],
        run_verified=True,
        agent_id="coder",
        model="deepseek-v4-flash",
    )
    gold = mg.select_gold([rec])
    assert gold[0]["tip_type"] == "recovery"
    pv = gold[0]["provenance"]
    assert pv["run_id"] == "g"
    assert pv["state_hashes"] == ["abc", "def"]
    assert pv["model"] == "deepseek-v4-flash"
    assert pv["verified"] is True
