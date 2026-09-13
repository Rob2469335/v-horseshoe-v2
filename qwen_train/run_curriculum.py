"""Verified tool-use curriculum runner for robs4b.

WHY (2026-09-13 research): a prompt list alone is the documented path to model
collapse / confabulation (ForTIFAI arXiv:2509.08972; "Honest Lying"
arXiv:2605.29463). What makes prompts usable as training signal is a HARD,
machine-checkable PASS/FAIL per item (RLVR / verified rejection sampling —
ToolBrain arXiv:2510.00023, TARL arXiv:2509.14480). So each curriculum item
carries a `verify` spec, and every run records `verified: true|false`.

This is the CURRICULUM + EVAL layer, not the trainer:
  - `train` split = candidate training tasks
  - `eval`  split = HELD-OUT gate (never train on it)
  - `target_tools` grades which tool(s) the task exercises
  - difficulty 1-5 grades the curriculum

Usage:
  python qwen_train/run_curriculum.py --next            # run the next unused item
  python qwen_train/run_curriculum.py --next --split eval
  python qwen_train/run_curriculum.py --id c02
  python qwen_train/run_curriculum.py --list
  python qwen_train/run_curriculum.py --stats

Results append to qwen_train/results/curriculum_runs.jsonl.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CURRICULUM = _HERE / "curriculum" / "tool_curriculum.jsonl"
RESULTS = _HERE / "results" / "curriculum_runs.jsonl"


def load_items() -> list[dict]:
    items: list[dict] = []
    if not CURRICULUM.exists():
        return items
    for line in CURRICULUM.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def verify(item: dict, content: str) -> dict:
    """Machine-checkable pass/fail for one item's answer. Pure; no I/O."""
    spec = item.get("verify") or {}
    vtype = spec.get("type", "contains")
    text = str(content or "").lower()
    if vtype == "contains":
        vals = [str(v).lower() for v in spec.get("value", [])]
        mode = spec.get("mode", "all")
        hits = [v for v in vals if v in text]
        if not vals:
            return {"passed": False, "reason": "no expected values"}
        passed = len(hits) == len(vals) if mode == "all" else len(hits) > 0
        return {"passed": passed, "reason": f"hits {hits} of {vals} (mode={mode})"}
    if vtype == "regex":
        import re

        pattern = str(spec.get("value", ""))
        hit = bool(re.search(pattern, content or "", re.IGNORECASE))
        return {"passed": hit, "reason": f"regex {pattern!r} -> {hit}"}
    if vtype == "manual":
        return {"passed": None, "reason": "manual review"}
    return {"passed": False, "reason": f"unknown verify type {vtype!r}"}


def extract_result(stdout: str) -> dict | None:
    """The CLI prints one JSON object last; return the last parseable one."""
    starts = [i for i, ch in enumerate(stdout) if ch == "{"]
    for i in reversed(starts):
        try:
            obj = json.loads(stdout[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "ok" in obj:
            return obj
    return None


def run_item(item: dict, timeout: int = 600) -> dict:
    """Run one item through the one-shot CLI and verify its answer."""
    prompt = item["prompt"]
    t0 = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "organism_console", "--json", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(_HERE.parent),
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        out = ""
        proc = None
    cli = extract_result(out)
    content = (cli or {}).get("content", "")
    check = verify(item, content)
    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    return {
        "ts": t0.isoformat(),
        "id": item["id"],
        "split": item.get("split", "train"),
        "difficulty": item.get("difficulty"),
        "target_tools": item.get("target_tools", []),
        "prompt": prompt,
        "cli_ok": bool((cli or {}).get("ok")),
        "verified": check.get("passed"),
        "verify_reason": check.get("reason"),
        "content": str(content)[:600],
        "elapsed_s": round(elapsed, 1),
    }


def _completed() -> dict[str, str]:
    """id -> iso ts of its most recent run."""
    seen: dict[str, str] = {}
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("id"):
                seen[rec["id"]] = rec.get("ts", "")
    return seen


def next_item(split: str = "all") -> dict | None:
    """The next item not yet run (a DIFFERENT one each time), wrapping when done."""
    items = load_items()
    if split != "all":
        items = [i for i in items if i.get("split") == split]
    if not items:
        return None
    done = _completed()
    for item in items:
        if item["id"] not in done:
            return item
    # all run: wrap to the least-recently-run
    return min(items, key=lambda i: done.get(i["id"], ""))


def record(result: dict) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False) + "\n")


def _print_stats() -> None:
    items = load_items()
    done = _completed()
    by_split: dict[str, list[int]] = {}
    for it in items:
        by_split.setdefault(it.get("split", "train"), []).append(1)
    print(
        f"items: {len(items)}  train: {len(by_split.get('train', []))}  "
        f"eval: {len(by_split.get('eval', []))}  run: {len(done)}"
    )
    if RESULTS.exists():
        recs = [
            json.loads(x) for x in RESULTS.read_text(encoding="utf-8").splitlines() if x
        ]
        verified = sum(1 for r in recs if r.get("verified") is True)
        graded = [r for r in recs if r.get("verified") is not None]
        rate = f"{verified}/{len(graded)}" if graded else "n/a"
        print(f"verified pass rate: {rate}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Verified tool-use curriculum runner")
    ap.add_argument("--next", action="store_true", help="run the next unused item")
    ap.add_argument("--id", help="run a specific item id")
    ap.add_argument("--split", default="all", choices=["all", "train", "eval"])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    if args.list:
        for it in load_items():
            print(
                f"{it['id']}\t{it.get('split')}\td{it.get('difficulty')}\t"
                f"{','.join(it.get('target_tools', []))}\t{it['prompt'][:70]}"
            )
        return 0
    if args.stats:
        _print_stats()
        return 0

    item = None
    if args.id:
        item = next((i for i in load_items() if i["id"] == args.id), None)
    elif args.next:
        item = next_item(args.split)
    if item is None:
        print("no item to run (use --next or --id, or the curriculum is empty)")
        return 1

    print(f"[{item['id']}] {item['prompt']}")
    result = run_item(item, timeout=args.timeout)
    record(result)
    mark = {True: "PASS", False: "FAIL", None: "MANUAL"}[result["verified"]]
    print(f"  cli_ok={result['cli_ok']}  verified={mark}  ({result['verify_reason']})")
    print(f"  content: {result['content'][:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
