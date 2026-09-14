"""Pathway-evidence metrics (docs/PATHWAY_EVIDENCE.md).

Proves *how* the CLI improved, not just *that* it did: did a stored lesson get
RETRIEVED, was the warned failure AVOIDED, and did the run reach a VERIFIED
success? (PAST-Bench arXiv:2608.04003; Harness-Updating-Not-Benefit arXiv:2605.30621.)

This module is pure and tolerant of the pathway capture not existing yet
(no `pathway` records => retrieval_rate 0, coherence None — honest, not fake).
The capture itself is written in stream_runner.py AFTER the 400-run
(mined-answer-drift rule). Raw trajectories are read-only here.
"""

from __future__ import annotations

import json
from pathlib import Path

_FAIL_LABELS = {"FAILURE", "ENVIRONMENT_FAILURE", "INELIGIBLE"}


def err_class(text: str) -> str:
    """Coarse error class so warned/observed signatures can be matched without
    brittle exact-string equality."""
    t = (text or "").lower()
    if "timeout" in t or "timed out" in t:
        return "timeout"
    if "not found" in t or "no such file" in t:
        return "not_found"
    if "permission" in t or "access is denied" in t or "denied" in t:
        return "permission"
    if "json" in t or "malformed" in t or "parse" in t:
        return "malformed"
    if "connection" in t or "readerror" in t:
        return "connection"
    return "other"


def load_run_full(path: Path) -> dict:
    """Split a trajectory file into steps + pathway records + summary."""
    steps, pathway, summary = [], [], None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            rt = rec.get("record_type")
            if rt == "step":
                steps.append(rec)
            elif rt == "pathway":
                pathway.append(rec)
            elif rt == "summary":
                summary = rec
    except Exception:
        return {"steps": [], "pathway": [], "summary": {}, "file": path.name}
    steps.sort(key=lambda r: int(r.get("step_id", 0) or 0))
    return {"steps": steps, "pathway": pathway, "summary": summary or {}, "file": path.name}


def warned_signatures(records: list[dict]) -> set[str]:
    out: set[str] = set()
    for p in records:
        if p.get("injected"):
            out |= set(p.get("warned_signatures") or [])
    return out


def step_failure_signatures(steps: list[dict]) -> set[str]:
    out: set[str] = set()
    for s in steps:
        res = (s.get("observation", {}).get("results") or [{}])[0]
        ex = res.get("extra") or {}
        if ex.get("label") in _FAIL_LABELS:
            tool = (s.get("tool_calls") or [{}])[0].get("function_name", "")
            out.add(f"{tool}:{err_class(ex.get('error'))}")
    return out


def run_metrics(run: dict) -> dict:
    injected = [p for p in run.get("pathway", []) if p.get("injected")]
    warned = warned_signatures(injected)
    observed = step_failure_signatures(run.get("steps", []))
    return {
        "retrieved": bool(injected),
        "warned": warned,
        "observed": observed,
        "avoided": bool(warned) and not (warned & observed),
        "verified": run.get("summary", {}).get("status") == "completed",
    }


def _rate(num: int, den: int):
    return round(num / den, 3) if den else None


def pathway_metrics(runs: list[dict]) -> dict:
    n = len(runs)
    if not n:
        return {"runs": 0, "note": "no runs"}
    ms = [run_metrics(r) for r in runs]
    warned_runs = [m for m in ms if m["warned"]]
    retrieved = [m for m in ms if m["retrieved"]]
    coherent = [m for m in warned_runs if m["avoided"] and m["verified"]]
    return {
        "runs": n,
        "runs_with_retrieval": len(retrieved),
        "retrieval_rate": _rate(len(retrieved), n),
        "runs_warned": len(warned_runs),
        "avoidance_rate": _rate(sum(1 for m in warned_runs if m["avoided"]), len(warned_runs)),
        "pathway_coherence_rate": _rate(len(coherent), len(warned_runs)),
        "success_with_retrieval": _rate(sum(1 for m in retrieved if m["verified"]), len(retrieved)),
        "success_without_retrieval": _rate(
            sum(1 for m in ms if not m["retrieved"] and m["verified"]), n - len(retrieved)
        ),
    }


def metrics_from_dir(traj_dir: Path) -> dict:
    runs = []
    for f in sorted(Path(traj_dir).glob("*.jsonl")):
        run = load_run_full(f)
        if run["steps"]:
            runs.append(run)
    return pathway_metrics(runs)
