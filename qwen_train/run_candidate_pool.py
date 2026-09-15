"""Run the merged candidate pool and MEASURE HARDNESS.

A candidate is "hard" only if the live agent FAILS it sometimes. This writes each
candidate's broken module + check into the sandbox, runs the CLI (coder) on it, and
verifies post-run (check exits 0 AND check.py unchanged). Results append to
results/curriculum_runs.jsonl with family="fix" and the taxonomy family recorded, so
pass rate per candidate/kind/family is computable.

Same backend preconditions as run_fix_tasks.py: SWARM_WRITE_ROOT set on the BACKEND,
backend up, DeepSeek-direct configured. Grants filesystem + sandbox_repl per run.

Usage: python qwen_train/run_candidate_pool.py --n 130 [--repeat 1]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import run_curriculum as rc  # noqa: E402

ROOT = _HERE.parent
SANDBOX = ROOT / "data" / "curriculum_fix"
POOL = _HERE / "curriculum" / "fix_pool_merged.jsonl"


def _make_task(cand: dict, idx: int) -> dict:
    d = SANDBOX / f"cand_{idx:03d}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "module.py").write_text(cand["broken"], encoding="utf-8")
    (d / "check.py").write_text(cand["check"], encoding="utf-8")
    sha = hashlib.sha256(cand["check"].encode("utf-8")).hexdigest()[:16]
    rel = str((d / "module.py").relative_to(ROOT)).replace("\\", "/")
    check_rel = str((d / "check.py").relative_to(ROOT)).replace("\\", "/")
    return {
        "id": f"cand{idx:03d}",
        "split": "train",
        "difficulty": cand.get("difficulty", 3),
        "target_tools": ["filesystem", "sandbox_repl"],
        "prompt": (
            f"Fix the file `{rel}` so that running `python {check_rel}` exits 0. "
            f"Read the check to see the expected behaviour, then patch ONLY the module. "
            f"Do NOT modify the check."
        ),
        "verify": {
            "type": "fix_file",
            "root": str(ROOT),
            "module": rel,
            "check": check_rel,
            "check_sha256": sha,
        },
        "kind": cand.get("kind", ""),
        "family_tax": cand.get("family", ""),
    }


def _verify(item: dict, timeout: int = 60) -> tuple[bool, str]:
    spec = item["verify"]
    cp = ROOT / spec["check"]
    if not cp.exists():
        return False, "check missing"
    if hashlib.sha256(cp.read_text(encoding="utf-8").encode("utf-8")).hexdigest()[:16] != spec["check_sha256"]:
        return False, "check.py modified (forbidden)"
    try:
        p = subprocess.run(
            [sys.executable, cp.name], cwd=str(cp.parent), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return False, "check timed out"
    return p.returncode == 0, (p.stdout or p.stderr).strip()[:200]


_record_lock = threading.Lock()


def _run_one(i: int, cand: dict, timeout: int) -> bool:
    """Measure ONE candidate: write broken sandbox -> run CLI -> verify. Thread-safe record."""
    item = _make_task(cand, i)
    res = rc.run_item(item, timeout=timeout, allow_approval=False)
    ok, reason = _verify(item)
    res["verified"] = ok
    res["verify_reason"] = reason
    res["family"] = "fix"
    res["kind"] = item["kind"]
    res["family_tax"] = item["family_tax"]
    with _record_lock:  # concurrent appends must not interleave
        rc.record(res)
    mark = "PASS" if ok else "FAIL"
    print(f"[{i + 1}] {item['kind']:32} {mark}  ({reason[:50]})", flush=True)
    return ok


async def _run_all(cands: list[dict], timeout: int, concurrency: int) -> int:
    sem = asyncio.Semaphore(concurrency)
    results: dict[int, bool] = {}

    async def _g(i: int, cand: dict) -> None:
        async with sem:
            results[i] = await asyncio.to_thread(_run_one, i, cand, timeout)

    await asyncio.gather(*[_g(i, c) for i, c in enumerate(cands)])
    return sum(1 for v in results.values() if v)


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure candidate-pool hardness")
    ap.add_argument("--pool", default=str(POOL))
    ap.add_argument("--n", type=int, default=130)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="run K items concurrently (the work is 99%% cloud-LLM wait, so K "
        "compresses wall-time; bounded by DeepSeek rate limits, not local CPU)",
    )
    args = ap.parse_args()

    cands = [json.loads(l) for l in Path(args.pool).read_text(encoding="utf-8").splitlines() if l.strip()]
    cands = cands[: args.n]
    print(f"pool: {len(cands)} candidates, concurrency={args.concurrency}, timeout={args.timeout}s")

    os.environ["SWARM_NO_TOASTS"] = "1"
    granted = False
    try:
        from swarm_os.services.trust_ledger import grant

        grant("filesystem", 12 * 3600)
        grant("sandbox_repl", 12 * 3600)
        granted = True
    except Exception as exc:  # noqa: BLE001
        print(f"grant failed: {exc}")

    try:
        if args.concurrency > 1:
            passed = asyncio.run(_run_all(cands, args.timeout, args.concurrency))
        else:
            passed = sum(_run_one(i, c, args.timeout) for i, c in enumerate(cands))
    finally:
        if granted:
            try:
                from swarm_os.services.trust_ledger import revoke

                revoke("filesystem")
                revoke("sandbox_repl")
            except Exception:  # noqa: BLE001
                pass

    n = len(cands)
    print(f"\nhardness measured: {passed}/{n} passed ({round(100 * passed / max(n, 1))}%)")
    print(f"first-attempt FAILURES (the 'hard' signal): {n - passed}/{n}")
    rc.progress()
    return 0


if __name__ == "__main__":
    sys.exit(main())
