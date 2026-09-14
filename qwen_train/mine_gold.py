"""Gold-set miner / diversity filter (RUN2_OBSERVATIONS.md step 8).

RAW     data/trajectories/*.jsonl            immutable experiment evidence (READ ONLY)
MINED   qwen_train/results/gold/mined_runs.jsonl     every valid run, classified
GOLD    qwen_train/results/gold/gold_candidates.jsonl curated training candidates
REPORT  qwen_train/results/gold/gold_report.json      the 6 go/no-go questions

This script NEVER writes to data/trajectories/ — the 400-run output is raw
evidence. Rerunning the filter is free; rerunning 400 trajectories is not. So the
mining algorithm is versioned here and applied to a frozen raw set.

Pipeline:
  valid -> classify(SUCCESS/FAILURE/INELIGIBLE/ENVIRONMENT) -> dedupe ->
  loop-quarantine -> tool-choice/state diversity cap -> critical-step candidates ->
  RECOVERY candidates -> GOLD.

Grounding: diversity over quantity (arXiv:2602.03219 TDScaling; 2026.findings-acl.768
Trajectory Diversity Scaling); recovery-as-gold (AgentHER arXiv:2603.21357);
critical-step mining (VPR arXiv:2605.10325; TRACE arXiv:2607.13988). `critical` and
`recovery` here are HEURISTIC candidates (deterministic, pre-ablation), not proven
causal steps — the ablation/teacher pass decides.

Honest scope: SUCCESS uses the run's own verifier (joined) when available; when a
run is not joinable to a curriculum item, it is NOT counted as a verified success
for gold (avoids promoting unverified trajectories).
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import mine_turns as mt  # noqa: E402

ROOT = _HERE.parent
TRAJ_DIR = ROOT / "data" / "trajectories"
OUT_DIR = _HERE / "results" / "gold"

_TRIGGERS = {"FAILURE", "ENVIRONMENT_FAILURE", "INELIGIBLE"}
_PER_SHAPE_CAP = 2  # diversity: keep at most N runs per exact tool-sequence shape


def _arg_seqs(run: dict) -> list[str]:
    return [
        mt._arg_sig((s.get("tool_calls") or [{}])[0].get("arguments") or {})
        for s in run["steps"]
    ]


def classify(rec: dict, env_failures: int) -> str:
    if rec.get("ineligible") or (rec["n_ineligible"] and not rec["succeeded"]):
        return "INELIGIBLE"
    if rec["succeeded"] and rec.get("run_verified") is not False:
        return "SUCCESS"
    if env_failures and not rec["succeeded"]:
        return "ENVIRONMENT"
    return "FAILURE"


def build_records(traj_dir: Path = TRAJ_DIR, runs: list[dict] | None = None) -> dict:
    runs = mt.load_targets() if runs is None else runs
    valid: list[dict] = []
    invalid = 0
    for f in sorted(traj_dir.glob("*.jsonl")):
        run = mt.load_run(f)
        if not run or not run["steps"]:
            invalid += 1
            continue
        rec = mt.analyze(run)
        rec["_mtime"] = f.stat().st_mtime
        rec["arg_seq"] = _arg_seqs(run)
        rec["sig"] = "|".join(rec["arg_seq"])
        rec["shape"] = ">".join([t for t in rec["tool_seq"] if t])
        rec["state_hashes"] = [
            ((s.get("observation", {}).get("results") or [{}])[0].get("extra") or {}).get(
                "state_hash", ""
            )
            for s in run["steps"]
        ]
        env = sum(1 for x in rec["labels"] if x == "ENVIRONMENT_FAILURE")
        joined = mt._join_target(rec, runs)
        if joined:
            rec["target_tools"] = joined.get("target_tools") or []
            rec["run_verified"] = joined.get("verified")
            rec["ineligible"] = joined.get("ineligible")
        rec["outcome"] = classify(rec, env)
        valid.append(rec)
    return {"valid": valid, "invalid": invalid}


def _rank(r: dict) -> tuple:
    """Canonical-selection order: verified successes first, then fewest steps."""
    verified_ok = bool(r["succeeded"] and r.get("run_verified") is not False)
    return (not verified_ok, r["n_steps"])


def dedupe(recs: list[dict]) -> tuple[list[dict], list[str]]:
    """Exact-argument duplicates collapse; the most efficient verified run wins."""
    seen: dict[tuple, dict] = {}
    removed: list[str] = []
    for r in sorted(recs, key=_rank):
        key = (r["shape"], r["sig"])
        if key in seen:
            removed.append(r["run_id"])
            continue
        seen[key] = r
    return list(seen.values()), removed


def cap_diversity(recs: list[dict], per_shape: int = _PER_SHAPE_CAP) -> tuple[list, list]:
    counts: collections.Counter = collections.Counter()
    kept, dropped = [], []
    for r in sorted(recs, key=_rank):
        if counts[r["shape"]] >= per_shape:
            dropped.append(r["run_id"])
            continue
        counts[r["shape"]] += 1
        kept.append(r)
    return kept, dropped


def critical_steps(rec: dict) -> list[int]:
    """Heuristic critical-step candidates (pre-ablation)."""
    labels = rec["labels"]
    tools = rec["tool_seq"]
    idx: set[int] = set()
    for i, lab in enumerate(labels):
        if lab in _TRIGGERS:
            for j in range(i + 1, len(labels)):
                if labels[j] == "NORMAL_SUCCESS" and tools[j] != tools[i]:
                    idx.add(j)
                    break
            break
    succ = [i for i, lab in enumerate(labels) if lab == "NORMAL_SUCCESS"]
    if succ:
        idx.add(succ[-1])
    return sorted(idx)


def select_gold(recs: list[dict]) -> list[dict]:
    """Tier = recovery (adapted after a failure) > critical (a real multi-tool
    choice) > success (single-tool straightforward). Failures/loops excluded."""
    gold: list[dict] = []
    for r in recs:
        if r["outcome"] != "SUCCESS" or r["loop"]:
            continue
        crit = critical_steps(r)
        if r["recovery"]:
            tier = "recovery"
        elif len({t for t in r["tool_seq"] if t}) > 1:
            tier = "critical"
        else:
            tier = "success"
        gold.append({**r, "critical_steps": crit, "tier": tier})
    order = {"recovery": 0, "critical": 1, "success": 2}
    gold.sort(key=lambda x: (order[x["tier"]], x["n_steps"]))
    return gold


def learning_trend(recs: list[dict]) -> dict:
    """Early-vs-late comparison over chronological order — does the SYSTEM get
    better within the batch? (The learner is the CLI/agent, not the weights.)"""
    ordered = sorted((r for r in recs if r.get("_mtime") is not None), key=lambda r: r["_mtime"])
    if len(ordered) < 4:
        return {"n": len(ordered), "note": "too few ordered runs for a trend"}

    def stats(part: list[dict]) -> dict:
        n = len(part)
        return {
            "runs": n,
            "success_rate": round(sum(1 for r in part if r["outcome"] == "SUCCESS") / n, 3),
            "avg_steps": round(sum(r["n_steps"] for r in part) / n, 2),
            "loop_rate": round(sum(1 for r in part if r["loop"]) / n, 3),
            "recovery_rate": round(sum(1 for r in part if r["recovery"]) / n, 3),
        }

    mid = len(ordered) // 2
    early, late = stats(ordered[:mid]), stats(ordered[mid:])
    delta = {
        k: round(late[k] - early[k], 3)
        for k in early
        if k != "runs" and isinstance(early[k], (int, float))
    }
    return {"n": len(ordered), "early": early, "late": late, "delta": delta}


def learning_curve(recs: list[dict], buckets: int = 5) -> dict:
    """North-star: does the CLI get better with MORE experience? Success rate
    over consecutive experience buckets (not just early/late halves)."""
    ordered = sorted((r for r in recs if r.get("_mtime") is not None), key=lambda r: r["_mtime"])
    n = len(ordered)
    if n < buckets:
        return {"n": n, "note": "too few for a curve"}
    size = max(1, n // buckets)
    out = []
    for i in range(0, n, size):
        part = ordered[i : i + size]
        if not part:
            continue
        out.append(
            {
                "experience_band": f"{i + 1}-{i + len(part)}",
                "success_rate": round(
                    sum(1 for r in part if r["outcome"] == "SUCCESS") / len(part), 3
                ),
                "avg_steps": round(sum(r["n_steps"] for r in part) / len(part), 2),
            }
        )
    return {"n": n, "buckets": out}


def self_healing(recs: list[dict]) -> dict:
    """Measurable self-healing: among runs that HIT a failure, how many became a
    verified success? (Hard definition — not "the AI tried again".)"""
    eligible = [r for r in recs if any(l in _TRIGGERS for l in r["labels"])]
    healed = [r for r in eligible if r["outcome"] == "SUCCESS"]
    return {
        "runs_with_a_failure": len(eligible),
        "became_verified_success": len(healed),
        "self_healing_rate": round(len(healed) / len(eligible), 3) if eligible else None,
    }


def stratified_trend(recs: list[dict]) -> dict:
    """Confound control for the early->late trend: if the task MIX changes across
    the batch, raw early/late success is partly composition, not learning. This
    standardizes success to the whole-batch shape distribution and reports both,
    so the raw delta can be checked against the mix-adjusted one."""
    ordered = sorted((r for r in recs if r.get("_mtime") is not None), key=lambda r: r["_mtime"])
    if len(ordered) < 4:
        return {"note": "too few ordered runs"}

    def raw(part: list[dict]) -> float:
        return round(sum(1 for r in part if r["outcome"] == "SUCCESS") / len(part), 3)

    all_shapes = collections.Counter(r["shape"] for r in ordered)
    total = sum(all_shapes.values())
    weights = {s: c / total for s, c in all_shapes.items()}

    def rate(part: list[dict], shape: str):
        sub = [r for r in part if r["shape"] == shape]
        if not sub:
            return None
        return sum(1 for r in sub if r["outcome"] == "SUCCESS") / len(sub)

    def adjusted(part: list[dict]):
        num = wsum = 0.0
        for s, w in weights.items():
            rr = rate(part, s)
            if rr is None:
                continue
            num += w * rr
            wsum += w
        return round(num / wsum, 3) if wsum else None

    mid = len(ordered) // 2
    early, late = ordered[:mid], ordered[mid:]
    re_, rl = raw(early), raw(late)
    ae, al = adjusted(early), adjusted(late)
    return {
        "raw_early": re_,
        "raw_late": rl,
        "raw_delta": round(rl - re_, 3),
        "mix_adjusted_early": ae,
        "mix_adjusted_late": al,
        "mix_adjusted_delta": (
            round(al - ae, 3) if ae is not None and al is not None else None
        ),
        "note": "raw_delta >> mix_adjusted_delta means the raw trend is partly task mix",
    }


def _tools_helped(recs: list[dict]) -> dict:
    used: collections.Counter = collections.Counter()
    helped: collections.Counter = collections.Counter()
    for r in recs:
        for f, lab in zip(r["tool_seq"], r["labels"]):
            if not f:
                continue
            used[f] += 1
            if lab == "NORMAL_SUCCESS":
                helped[f] += 1
    return {
        t: {"used": used[t], "success": helped[t], "rate": round(helped[t] / used[t], 2)}
        for t in used
    }


def run_filter(traj_dir: Path = TRAJ_DIR) -> dict:
    built = build_records(traj_dir)
    valid = built["valid"]

    outcomes = collections.Counter(r["outcome"] for r in valid)
    successes = [r for r in valid if r["outcome"] == "SUCCESS"]

    deduped, dup_removed = dedupe(valid)
    diverse, div_removed = cap_diversity(deduped)
    gold = select_gold(diverse)

    shapes = collections.Counter(r["shape"] for r in successes)
    report = {
        "raw_files_scanned": len(list(traj_dir.glob("*.jsonl"))),
        "valid": len(valid),
        "invalid_or_empty": built["invalid"],
        "outcomes": dict(outcomes),
        "success": len(successes),
        "success_diversity": {
            "distinct_tool_shapes": len(shapes),
            "distinct_arg_signatures": len({r["sig"] for r in successes}),
            "distinct_state_hash_seqs": len(
                {tuple(r.get("state_hashes") or []) for r in successes}
            ),
            "top_shapes": shapes.most_common(5),
        },
        "tools_helped": _tools_helped(valid),
        "recovery_runs": sum(1 for r in valid if r["recovery"]),
        "critical_candidates": sum(len(critical_steps(r)) for r in valid),
        "loop_runs": sum(1 for r in valid if r["loop"]),
        "dedupe_removed": len(dup_removed),
        "diversity_removed": len(div_removed),
        "gold": len(gold),
        "gold_tiers": dict(collections.Counter(g["tier"] for g in gold)),
        "learning_trend": learning_trend(valid),
        "learning_curve": learning_curve(valid),
        "self_healing": self_healing(valid),
        "stratified_trend": stratified_trend(valid),
    }
    return {"report": report, "valid": valid, "gold": gold}


def write_outputs(result: dict, out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "mined_runs.jsonl", "w", encoding="utf-8") as fh:
        for r in result["valid"]:
            json.dump(r, fh, ensure_ascii=False)
            fh.write("\n")
    with open(out_dir / "gold_candidates.jsonl", "w", encoding="utf-8") as fh:
        for g in result["gold"]:
            json.dump(g, fh, ensure_ascii=False)
            fh.write("\n")
    with open(out_dir / "gold_report.json", "w", encoding="utf-8") as fh:
        json.dump(result["report"], fh, indent=2, ensure_ascii=False)


def main() -> int:
    result = run_filter()
    write_outputs(result)
    rep = result["report"]
    print("=== GOLD REPORT (6 go/no-go questions) ===")
    print(f"raw files scanned        : {rep['raw_files_scanned']}")
    print(f"valid / invalid          : {rep['valid']} / {rep['invalid_or_empty']}")
    print(f"outcomes                 : {rep['outcomes']}")
    print(f"1. successful runs       : {rep['success']}")
    print(f"2. success diversity     : {rep['success_diversity']}")
    print("3. tools that helped     :")
    for t, d in sorted(rep["tools_helped"].items(), key=lambda kv: -kv[1]["rate"]):
        print(f"     {t:<12} used={d['used']:<4} success={d['success']:<4} rate={d['rate']}")
    print(f"4. recovery runs         : {rep['recovery_runs']}")
    print(f"5. critical candidates   : {rep['critical_candidates']}")
    print(f"6. near-dupes removed    : {rep['dedupe_removed']}  (+diversity cap {rep['diversity_removed']})")
    print(f"loops quarantined        : {rep['loop_runs']}")
    print(f"GOLD candidates          : {rep['gold']}  {rep['gold_tiers']}")
    print("\n=== SYSTEM LEARNING TREND (early vs late half) ===")
    tr = rep.get("learning_trend", {})
    if "early" in tr:
        print(f"  early: {tr['early']}")
        print(f"  late : {tr['late']}")
        print(f"  delta: {tr['delta']}")
        print("  (success_rate up / avg_steps down / loop_rate down = the system learned)")
    else:
        print(f"  {tr}")
    print("\n=== NORTH STAR: learning curve over experience ===")
    for b in rep.get("learning_curve", {}).get("buckets", []):
        print(
            f"  runs {b['experience_band']:<9} success={b['success_rate']:<6} avg_steps={b['avg_steps']}"
        )
    sh = rep.get("self_healing", {})
    print(
        f"\n=== SELF-HEALING (hard def) ===\n"
        f"  runs that hit a failure : {sh.get('runs_with_a_failure')}\n"
        f"  recovered to verified   : {sh.get('became_verified_success')}\n"
        f"  self-healing rate       : {sh.get('self_healing_rate')}"
    )
    st = rep.get("stratified_trend", {})
    print(
        f"\n=== CONFOUND CONTROL (task-mix adjusted) ===\n"
        f"  raw delta               : {st.get('raw_delta')}\n"
        f"  mix-adjusted delta      : {st.get('mix_adjusted_delta')}\n"
        f"  ({st.get('note', '')})"
    )
    print(f"\nwrote -> {(OUT_DIR / 'gold_candidates.jsonl').relative_to(ROOT)}")
    if rep["success"] < 30:
        print("  -> success count is low / run still in flight; re-run after the batch finishes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
