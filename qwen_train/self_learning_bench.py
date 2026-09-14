"""Self-Learning Score + paired significance test (docs/EXPERIMENTS.md, docs/SOTA_ROADMAP.md).

Two jobs:
1. Paired significance for T1->T2 on the SAME frozen 60 (McNemar exact + bootstrap CI),
   with a PRE-REGISTERED minimum effect. The headline caution from the research: a
   positive holdout delta is not a win unless it clears noise (Hermes self-evolution
   issue #135 declares any positive delta a win; GEPA runs on as few as 3 holdout
   examples where +0.02 is indistinguishable from noise).
2. A composite Self-Learning Score over the experiment outputs, reporting which
   components are present (missing components are NOT silently counted as 0).

Read-only over existing result files. Emits its own report.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
RESULTS = _HERE / "results"
GOLD_REPORT = RESULTS / "gold" / "gold_report.json"
MEMORY_COMPARE = RESULTS / "memory_ablation_compare.json"

# Pre-registered effect size we will not call a win below (see docs).
MIN_EFFECT = 0.05
ALPHA = 0.05


# ── statistics ───────────────────────────────────────────────────────────────
def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p over discordant pairs (b: fail->pass, c: pass->fail)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2.0 * tail)


def bootstrap_delta_ci(pairs: list[tuple[bool, bool]], n_boot: int = 5000, seed: int = 0):
    """95% CI for (T2 rate - T1 rate) by resampling the MATCHED items."""
    n = len(pairs)
    if n == 0:
        return (None, None)
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_boot):
        s = [pairs[rng.randrange(n)] for _ in range(n)]
        deltas.append(sum(1 for _, y in s if y) / n - sum(1 for x, _ in s if x) / n)
    deltas.sort()
    return (round(deltas[int(0.025 * n_boot)], 3), round(deltas[int(0.975 * n_boot) - 1], 3))


def paired_stats(t1: dict, t2: dict, min_effect: float = MIN_EFFECT, alpha: float = ALPHA) -> dict:
    """Paired T1->T2 on matched item ids. Verdict requires p<alpha AND |delta|>=min_effect."""
    ids = sorted(set(t1) & set(t2))
    pairs = [(bool(t1[i]), bool(t2[i])) for i in ids]
    n = len(pairs)
    if not n:
        return {"n": 0, "note": "no matched ids between T1 and T2"}
    a = sum(1 for x, y in pairs if x and y)
    b = sum(1 for x, y in pairs if not x and y)
    c = sum(1 for x, y in pairs if x and not y)
    d = sum(1 for x, y in pairs if not x and not y)
    r1, r2 = (a + c) / n, (a + b) / n
    delta = r2 - r1
    p = mcnemar_exact_p(b, c)
    lo, hi = bootstrap_delta_ci(pairs)
    if p < alpha and delta >= min_effect:
        verdict = "win"
    elif p < alpha and delta <= -min_effect:
        verdict = "loss"
    else:
        verdict = "no_signal"
    return {
        "n": n,
        "t1_rate": round(r1, 3),
        "t2_rate": round(r2, 3),
        "delta": round(delta, 3),
        "discordant_fail_to_pass": b,
        "discordant_pass_to_fail": c,
        "concordant_both_pass": a,
        "concordant_both_fail": d,
        "mcnemar_exact_p": round(p, 4),
        "boot_ci95": [lo, hi],
        "min_effect": min_effect,
        "alpha": alpha,
        "verdict": verdict,
        "note": "verdict requires p<alpha AND |delta|>=pre-registered min_effect",
    }


# ── composite score ──────────────────────────────────────────────────────────
def composite_score(parts: dict) -> dict:
    """parts: {name: value in [0,1] or None}. Missing (None) components are listed,
    NOT counted as 0 — an absent signal must not look like a bad score."""
    present = {k: v for k, v in parts.items() if isinstance(v, (int, float))}
    missing = [k for k, v in parts.items() if v is None]
    score = round(100 * sum(present.values()) / len(present), 1) if present else None
    return {"score": score, "components_present": present, "components_missing": missing}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def score_from_reports(gold: dict | None = None, memory: dict | None = None) -> dict:
    gold = _load_json(GOLD_REPORT) if gold is None else gold
    memory = _load_json(MEMORY_COMPARE) if memory is None else memory
    sh = gold.get("self_healing", {}).get("self_healing_rate")
    trend = gold.get("stratified_trend", {})
    learning = (trend.get("mix_adjusted_delta") or 0) + 0.5  # center ~0 delta at 0.5
    learning = max(0.0, min(1.0, learning))
    diversity = None
    succ = gold.get("success")
    if succ:
        diversity = min(1.0, gold.get("success_diversity", {}).get("distinct_tool_shapes", 0) / 6)
    mem_delta = memory.get("delta_on_minus_off", {}).get("success_rate")
    parts = {
        "learning_mix_adjusted": learning if trend else None,
        "self_healing": sh,
        "memory_utility": (mem_delta + 0.5) if mem_delta is not None else None,
        "tool_shape_diversity": diversity,
    }
    return composite_score(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Self-Learning Score + paired significance")
    ap.add_argument("--t1", help="T1 result json {id: verified}")
    ap.add_argument("--t2", help="T2 result json {id: verified}")
    ap.add_argument("--min-effect", type=float, default=MIN_EFFECT)
    args = ap.parse_args()

    if args.t1 and args.t2:
        t1 = _load_json(Path(args.t1))
        t2 = _load_json(Path(args.t2))
        res = paired_stats(t1, t2, min_effect=args.min_effect)
        print(json.dumps(res, indent=2))
        return 0

    print("=== SELF-LEARNING SCORE ===")
    print(json.dumps(score_from_reports(), indent=2))
    print("\n(pass --t1 a.json --t2 b.json for the paired T1->T2 significance test)")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
