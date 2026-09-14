"""Tests for the memory ablation harness (qwen_train/run_memory_ablation.py).

Pins the comparison math + the deterministic arm-matching id selection (both
arms must see identical tasks). The harness is NOT run here — Experiment B runs
only after the 400-run (docs/EXPERIMENTS.md).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import run_memory_ablation as ma  # noqa: E402


def _rec(verified=True, tool_hit=True, ineligible=False, cli_ok=True, tools=("filesystem",)):
    return {
        "verified": verified,
        "tool_hit": tool_hit,
        "ineligible": ineligible,
        "cli_ok": cli_ok,
        "tools_used": list(tools),
    }


def test_summarize_rates():
    recs = [_rec(), _rec(verified=False, tool_hit=False)]
    s = ma.summarize(recs)
    assert s["runs"] == 2
    assert s["success_rate"] == 0.5
    assert s["tool_hit_rate"] == 0.5
    assert s["avg_tools"] == 1.0


def test_summarize_empty():
    assert ma.summarize([]) == {}


def test_compare_delta_sign():
    on = [_rec(), _rec()]  # 100% success
    off = [_rec(), _rec(verified=False, tool_hit=False)]  # 50%
    res = ma.compare(on, off)
    assert res["memory_on"]["success_rate"] == 1.0
    assert res["memory_off"]["success_rate"] == 0.5
    # positive delta == memory ON is better
    assert res["delta_on_minus_off"]["success_rate"] == 0.5


def test_fixed_ids_deterministic_and_seed_sensitive():
    a = ma.fixed_ids(10, 7)
    b = ma.fixed_ids(10, 7)
    c = ma.fixed_ids(10, 8)
    assert a == b, "same seed must select the same tasks for both arms"
    assert len(a) == 10
    assert a != c, "different seed should sample a different task set"
