"""Mine per-turn trajectory steps into the recovery / critical-step / tool-choice
signal (docs/RUN2_OBSERVATIONS.md priorities #1/#4/#5).

Input  : data/trajectories/<run_id>.jsonl  — ATIF-shaped `step` records written by
         AgentServiceV2._write_run_step, followed by the run `summary`.
Join   : qwen_train/results/curriculum_runs.jsonl (optional) on task-prefix, for
         the item's `target_tools` + run-level `verified`.
Output : qwen_train/results/turn_mining.jsonl + a printed recipe readout.

Why this exists (grounding):
  - `recovery` was a run-level false positive (RUN2_OBSERVATIONS #1): "verified AND
    used-target" counts habitual tool use. The real definition needs the step
    sequence — a tool FAILED/DENIED, then a LATER *different* tool succeeded.
  - critical-step mining (VPR arXiv:2605.10325; TRACE 2607.13988) and recovery-as-
    gold (AgentHER arXiv:2603.21357) both operate on per-turn records, not finals.
  - tool-selection is scored on the item's intended tool vs the one actually used.

Honest scope: `recovery` here is deterministic from labels + tool sequence. It does
NOT prove the recovered step was causally necessary — that is what the downstream
critical-step/RFT pass decides. This tool only labels, counts, and ranks.
"""

from __future__ import annotations

import collections
import hashlib
import json
import statistics
import sys
from pathlib import Path

from _atomic import atomic_write_jsonl

ROOT = Path(__file__).resolve().parent.parent
TRAJ_DIR = ROOT / "data" / "trajectories"
RUNS = ROOT / "qwen_train" / "results" / "curriculum_runs.jsonl"
OUT = ROOT / "qwen_train" / "results" / "turn_mining.jsonl"

_FAILURE_LABELS = {"FAILURE", "ENVIRONMENT_FAILURE"}
_RECOVERY_TRIGGERS = {"FAILURE", "ENVIRONMENT_FAILURE", "INELIGIBLE"}


def _arg_sig(args: dict) -> str:
    blob = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def load_run(path: Path) -> dict | None:
    """Split a trajectory file into its ordered steps + its summary record."""
    steps: list[dict] = []
    summary: dict | None = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("record_type") == "step":
                steps.append(rec)
            elif rec.get("record_type") == "summary":
                summary = rec
    except Exception:
        return None
    if summary is None and not steps:
        return None
    steps.sort(key=lambda r: int(r.get("step_id", 0) or 0))
    return {"steps": steps, "summary": summary or {}, "file": path.name}


def analyze(run: dict) -> dict:
    steps = run["steps"]
    summary = run["summary"]
    tools: list[str] = []
    labels: list[str] = []
    sigs: list[str] = []
    turns: list[int] = []
    for s in steps:
        tc = (s.get("tool_calls") or [{}])[0]
        tools.append(str(tc.get("function_name") or ""))
        res = (s.get("observation", {}).get("results") or [{}])[0]
        ex = res.get("extra") or {}
        labels.append(str(ex.get("label") or "UNKNOWN"))
        sigs.append(_arg_sig(tc.get("arguments") or {}))
        turns.append(int(ex.get("state", {}).get("turn", 0) or 0))

    # loop = an identical (tool, args) call repeated back-to-back.
    loop = any(
        tools[i] == tools[i - 1] and sigs[i] == sigs[i - 1]
        for i in range(1, len(tools))
    )

    # recovery = a trigger step, then a LATER *different* tool that succeeded.
    recovery = False
    for i, lab in enumerate(labels):
        if lab not in _RECOVERY_TRIGGERS:
            continue
        for j in range(i + 1, len(labels)):
            if labels[j] == "NORMAL_SUCCESS" and tools[j] != tools[i]:
                recovery = True
                break
        if recovery:
            break

    success_idxs = [i for i, lab in enumerate(labels) if lab == "NORMAL_SUCCESS"]
    return {
        "run_id": run.get("run_id")
        or (summary.get("run_id") if summary else "")
        or run["file"].replace(".jsonl", ""),
        "task": str(summary.get("task") or "")[:300],
        "agent_id": summary.get("agent_id", ""),
        "status": summary.get("status", "unknown"),
        "succeeded": summary.get("status") == "completed",
        "n_steps": len(steps),
        "distinct_tools": len(set(t for t in tools if t)),
        "tool_seq": tools,
        "labels": labels,
        "turns": turns,
        "n_failure": sum(1 for x in labels if x in _FAILURE_LABELS),
        "n_ineligible": sum(1 for x in labels if x == "INELIGIBLE"),
        "recovery": recovery,
        "loop": loop,
        "calls_to_success": (success_idxs[0] + 1) if success_idxs else None,
    }


def load_targets() -> list[dict]:
    """curriculum_runs records (for the optional item join)."""
    out: list[dict] = []
    if not RUNS.exists():
        return out
    for line in RUNS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _join_target(rec: dict, runs: list[dict]) -> dict | None:
    task = rec["task"]
    if not task:
        return None
    for r in runs:
        p = str(r.get("prompt") or "")
        if p[:300] == task or p == task:
            return r
    return None


def main() -> int:
    files = sorted(TRAJ_DIR.glob("*.jsonl"))
    runs = load_targets()
    records: list[dict] = []
    for f in files:
        run = load_run(f)
        if not run or not run["steps"]:
            continue  # no per-turn data (pre-capture runs) — skip honestly
        rec = analyze(run)
        joined = _join_target(rec, runs)
        if joined:
            tgt = joined.get("target_tools") or []
            rec["target_tools"] = tgt
            rec["run_verified"] = joined.get("verified")
            first = next((t for t in rec["tool_seq"] if t), None)
            rec["tool_choice_hit"] = bool(tgt and first in tgt)
            # run-level `recovery` flag recorded by the runner, for the honesty
            # comparison against the step-derived one.
            rec["recovery_flag_runlevel"] = joined.get("recovery")
        records.append(rec)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_jsonl(OUT, records)

    labels = collections.Counter()
    rec_tools = collections.Counter()
    for rec in records:
        labels.update(rec["labels"])
        rec_tools.update(t for t in rec["tool_seq"] if t)

    with_steps = len(records)
    recovered = [r for r in records if r["recovery"]]
    recovered_ok = [r for r in recovered if r["succeeded"]]
    loops = [r for r in records if r["loop"]]
    joined = [r for r in records if "tool_choice_hit" in r]
    cts = [r["calls_to_success"] for r in records if r["calls_to_success"]]

    print(f"runs with per-turn data : {with_steps}")
    print(f"total steps             : {sum(r['n_steps'] for r in records)}")
    print(f"label histogram         : {dict(labels)}")
    print(f"tool histogram          : {dict(rec_tools)}")
    print(f"recovery (step-derived) : {len(recovered)}  (of which succeeded: {len(recovered_ok)})")
    if joined:
        flips = [
            r
            for r in joined
            if r.get("run_verified") and r.get("recovery_flag_runlevel") and not r["recovery"]
        ]
        print(
            f"run-level recovery flag : {sum(1 for r in joined if r.get('recovery_flag_runlevel'))} "
            f"-> step-derived {sum(1 for r in joined if r['recovery'])}  "
            f"(false positives removed: {len(flips)})"
        )
        hits = sum(1 for r in joined if r.get("tool_choice_hit"))
        print(f"tool-choice hit         : {hits}/{len(joined)}")
    if cts:
        print(
            f"calls_to_success        : mean {statistics.mean(cts):.1f}  "
            f"median {statistics.median(cts):.0f}  n={len(cts)}"
        )
    print(f"loops                   : {len(loops)}")
    print(f"\nwrote -> {OUT.relative_to(ROOT)}")
    if with_steps < 30:
        print(
            "  -> few runs have step records yet (capture is new); "
            "collect a run before reading the recipe."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
