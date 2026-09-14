"""Tests for the Self-Learning Score + paired significance (qwen_train/self_learning_bench.py).

The critical invariant: a positive T1->T2 delta is NOT a win unless it clears the
pre-registered minimum effect AND is significant on the paired (matched-item) test.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import self_learning_bench as slb  # noqa: E402


def test_mcnemar_exact_p_symmetric_and_significant():
    assert slb.mcnemar_exact_p(5, 5) == 1.0  # no asymmetry -> no evidence
    assert slb.mcnemar_exact_p(10, 0) < 0.01  # strong one-sided discordance
    assert slb.mcnemar_exact_p(0, 0) == 1.0


def test_paired_stats_win_requires_effect_and_significance():
    # 60 matched items; T1 passes 41, T2 passes 50 (9 fail->pass, 0 regressions)
    t1 = {str(i): (i < 41) for i in range(60)}
    t2 = {str(i): (i < 50) for i in range(60)}
    res = slb.paired_stats(t1, t2, min_effect=0.05)
    assert res["n"] == 60
    assert res["discordant_fail_to_pass"] == 9
    assert res["discordant_pass_to_fail"] == 0
    assert res["delta"] == 0.15
    assert res["mcnemar_exact_p"] < 0.05
    assert res["verdict"] == "win"


def test_paired_stats_tiny_delta_is_no_signal_even_if_significant():
    # a +0.02 move must NOT be called a win (Hermes #135 caution)
    t1 = {str(i): (i < 20) for i in range(60)}
    t2 = {str(i): (i < 21) for i in range(60)}  # 1 more pass
    res = slb.paired_stats(t1, t2, min_effect=0.05)
    assert res["delta"] == 0.017
    assert res["verdict"] == "no_signal"


def test_paired_stats_identical_is_no_signal():
    t = {str(i): (i < 30) for i in range(60)}
    assert slb.paired_stats(t, dict(t))["verdict"] == "no_signal"


def test_bootstrap_ci_brackets_delta():
    pairs = [(False, True)] * 9 + [(True, True)] * 41 + [(False, False)] * 10
    lo, hi = slb.bootstrap_delta_ci(pairs, n_boot=1000, seed=1)
    assert lo <= 0.15 <= hi


def test_composite_score_lists_missing_not_zero():
    out = slb.composite_score({"a": 1.0, "b": None, "c": 0.5})
    assert out["score"] == 75.0  # mean of present only (1.0, 0.5)
    assert "b" in out["components_missing"]
    assert out["components_present"] == {"a": 1.0, "c": 0.5}


def test_composite_score_all_missing():
    out = slb.composite_score({"a": None})
    assert out["score"] is None
