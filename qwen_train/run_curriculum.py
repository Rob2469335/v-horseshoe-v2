"""Verified tool-use curriculum → driver for the SWARM harness's own learning.

WHAT THIS IS FOR (clarified 2026-09-13): we are NOT training robs4b's weights
here. Every run through the agent loop already feeds the *harness's* learning
system — `SWARM_EVOLUTION=1` makes `agent_service_v2._feed_outcome` call
`outcome_fitness.record_outcome`, and tool ordering comes from the learned
`get_active_genome().tool_genes`. This runner makes those experiences DIVERSE
and MEASURABLE so that, by ~500 runs, the system has learned which tools to call.

It records per run:
  - `verified`     — a hard machine-check on the answer (RLVR-style, so the
                     signal is trustworthy and cannot be confabulated)
  - `tools_used`   — parsed from the stream (which tools the agent actually called)
  - `tool_hit`/`tool_all` — did it use the intended tool(s) for this task shape

Usage:
  python qwen_train/run_curriculum.py --next            # run the next unused item
  python qwen_train/run_curriculum.py --next --split eval
  python qwen_train/run_curriculum.py --id c02
  python qwen_train/run_curriculum.py --gen 200 --seed 7  # add 200 verified variants
  python qwen_train/run_curriculum.py --progress          # runs toward 500 + tool_genes
  python qwen_train/run_curriculum.py --list

Results append to qwen_train/results/curriculum_runs.jsonl.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CURRICULUM = _HERE / "curriculum" / "tool_curriculum.jsonl"
GENERATED = _HERE / "curriculum" / "generated.jsonl"
RESULTS = _HERE / "results" / "curriculum_runs.jsonl"

_TARGET_RUNS = 500
_TOOL_RE = re.compile(r"[⚡✓▶]\s+([a-z_][a-z0-9_]*)")


def load_items() -> list[dict]:
    items: list[dict] = []
    for path in (CURRICULUM, GENERATED):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
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
        pattern = str(spec.get("value", ""))
        hit = bool(re.search(pattern, content or "", re.IGNORECASE))
        return {"passed": hit, "reason": f"regex {pattern!r} -> {hit}"}
    if vtype == "manual":
        return {"passed": None, "reason": "manual review"}
    return {"passed": False, "reason": f"unknown verify type {vtype!r}"}


def parse_tools_used(stdout: str) -> list[str]:
    """Tool names the agent called, parsed from the live-stream markers."""
    return sorted(set(_TOOL_RE.findall(stdout or "")))


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


def _tool_match(item: dict, used: list[str]) -> tuple[bool, bool]:
    targets = set(item.get("target_tools") or [])
    got = set(used)
    return bool(targets & got), bool(targets and targets <= got)


def run_item(item: dict, timeout: int = 600) -> dict:
    """Run one item through the one-shot CLI and verify its answer + tool use."""
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
    cli = extract_result(out)
    content = (cli or {}).get("content", "")
    used = parse_tools_used(out)
    check = verify(item, content)
    # Feed the harness's contextual tool policy (per-shape verified experience).
    try:
        from runtime_v2.services.tool_policy import record_observation

        record_observation(prompt, used, check.get("passed"))
    except Exception:  # noqa: BLE001
        pass
    hit, all_hit = _tool_match(item, used)
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
        "tools_used": used,
        "tool_hit": hit,
        "tool_all": all_hit,
        "content": str(content)[:600],
        "elapsed_s": round(elapsed, 1),
    }


# --------------------------------------------------------------------------
# verified variant generator — scale to 500 with REAL diversity, not repeats
# --------------------------------------------------------------------------

_PI_CACHE: dict[int, int] = {}


def _primes_below(n: int) -> int:
    if n in _PI_CACHE:
        return _PI_CACHE[n]
    count = 0
    for k in range(2, n):
        if all(k % d for d in range(2, int(k**0.5) + 1)):
            count += 1
    _PI_CACHE[n] = count
    return count


def _fib(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def _make_variant(idx: int, rng: random.Random) -> dict:
    family = rng.choice(["mul", "sum", "primes", "fib", "gcd"])
    if family == "mul":
        a, b = rng.randint(12, 99), rng.randint(12, 99)
        prompt = (
            f"Use sandbox_repl to compute {a} * {b} and report the result as a number."
        )
        answer = a * b
    elif family == "sum":
        n = rng.randint(20, 200)
        prompt = f"Use sandbox_repl to report the sum of all integers from 1 to {n}."
        answer = n * (n + 1) // 2
    elif family == "primes":
        n = rng.randint(30, 120)
        prompt = f"Use sandbox_repl to count how many prime numbers are below {n}."
        answer = _primes_below(n)
    elif family == "fib":
        n = rng.randint(8, 20)
        prompt = (
            f"Use sandbox_repl to compute the {n}th Fibonacci number where F(1)=1 "
            "and F(2)=1, and report it."
        )
        answer = _fib(n)
    else:
        a, b = rng.randint(100, 9999), rng.randint(100, 9999)
        prompt = (
            f"Use sandbox_repl to compute the greatest common divisor of {a} and {b}."
        )
        answer = math.gcd(a, b)
    return {
        "id": f"g{idx:04d}",
        "split": "train",
        "difficulty": 1 if family in ("mul", "sum") else 2,
        "target_tools": ["sandbox_repl"],
        "prompt": prompt,
        "verify": {"type": "contains", "mode": "all", "value": [str(answer)]},
    }


def generate(n: int, seed: int = 0) -> int:
    """Append N verified variants to the generated pool. Returns count written."""
    existing = {i["id"] for i in load_items()}
    rng = random.Random(seed)
    written = 0
    GENERATED.parent.mkdir(parents=True, exist_ok=True)
    with GENERATED.open("a", encoding="utf-8") as fh:
        idx = len(existing)
        made = 0
        while made < n:
            idx += 1
            item = _make_variant(idx, rng)
            if item["id"] in existing:
                continue
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            existing.add(item["id"])
            made += 1
            written += 1
    return written


# --------------------------------------------------------------------------
# learning progress
# --------------------------------------------------------------------------


def _completed() -> dict[str, str]:
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
    return min(items, key=lambda i: done.get(i["id"], ""))


def record(result: dict) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False) + "\n")


def progress() -> None:
    items = load_items()
    done = _completed()
    print(f"curriculum items: {len(items)}  (ran {len(done)}; target {_TARGET_RUNS})")
    if not RESULTS.exists():
        print("no runs recorded yet")
        return
    recs = [
        json.loads(x)
        for x in RESULTS.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    graded = [r for r in recs if r.get("verified") is not None]
    verified = sum(1 for r in graded if r.get("verified"))
    hit = sum(1 for r in recs if r.get("tool_hit"))
    used: set[str] = set()
    for r in recs:
        used.update(r.get("tools_used") or [])
    print(
        f"verified pass: {verified}/{len(graded)}  "
        f"tool-hit: {hit}/{len(recs)}  distinct tools exercised: {len(used)}"
    )
    print(f"tools exercised: {sorted(used)}")
    try:
        from swarm_os.services.evolution_daemon import get_active_genome

        gid, weights = get_active_genome(False)
        top = dict(sorted(weights.items(), key=lambda kv: -kv[1])[:8])
        print(f"learned tool policy ({gid}): {top}")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not read learned tool policy: {exc})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Verified tool-use curriculum driver")
    ap.add_argument("--next", action="store_true", help="run the next unused item")
    ap.add_argument("--id", help="run a specific item id")
    ap.add_argument("--split", default="all", choices=["all", "train", "eval"])
    ap.add_argument("--gen", type=int, metavar="N", help="append N verified variants")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    if args.gen:
        n = generate(args.gen, args.seed)
        print(f"generated {n} verified variant(s) -> {GENERATED}")
        return 0
    if args.list:
        for it in load_items():
            print(
                f"{it['id']}\t{it.get('split')}\td{it.get('difficulty')}\t"
                f"{','.join(it.get('target_tools', []))}\t{it['prompt'][:70]}"
            )
        return 0
    if args.progress:
        progress()
        return 0

    item = None
    if args.id:
        item = next((i for i in load_items() if i["id"] == args.id), None)
    elif args.next:
        item = next_item(args.split)
    if item is None:
        print("no item to run (use --next or --id, or generate with --gen)")
        return 1

    print(f"[{item['id']}] {item['prompt']}")
    result = run_item(item, timeout=args.timeout)
    record(result)
    mark = {True: "PASS", False: "FAIL", None: "MANUAL"}[result["verified"]]
    print(
        f"  cli_ok={result['cli_ok']}  verified={mark}  "
        f"tool_hit={result['tool_hit']}  tools_used={result['tools_used']}"
    )
    print(f"  content: {result['content'][:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
