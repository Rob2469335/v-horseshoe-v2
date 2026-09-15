"""Memory ablation harness (Experiment B, docs/EXPERIMENTS.md).

Question: does the memory system actually contribute to outcomes? Same tasks,
same model/tools/permissions/verification — only memory injection differs.

CRITICAL (learned the hard way): memory injection runs in the BACKEND
(runtime_v2/services/stream_runner.py), NOT in the CLI subprocess. So the flag
must be set when the BACKEND is launched; setting it on the CLI does nothing.

  arm ON  : start the backend normally (SWARM_MEMORY_INJECT unset / =1)
  arm OFF : start the backend with  SWARM_MEMORY_INJECT=0

  python qwen_train/run_memory_ablation.py --arm on  --n 50
  # restart backend with the off flag
  python qwen_train/run_memory_ablation.py --arm off --n 50
  python qwen_train/run_memory_ablation.py --compare

Both arms use the SAME deterministic id list (--seed) so task distribution is
identical. Writes only to qwen_train/results/ — never touches the 400-run data.

Precondition: the `SWARM_MEMORY_INJECT` gate in stream_runner.py must exist
(applied AFTER the 400-run finishes, per the mined-answer-drift rule). This
harness is prepared now and run later.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import run_curriculum as rc  # noqa: E402
from _atomic import atomic_write_json  # noqa: E402

RESULTS = _HERE / "results"


def fixed_ids(n: int, seed: int) -> list[str]:
    """Deterministic sample of APPROVAL-FREE train item ids — identical for both arms.

    Restricted to approval-free tools (rc._APPROVAL_FREE) because the pool is ~90%
    `sandbox_repl` (ALWAYS_CONFIRM): those tasks trigger the CLI approval prompt and
    make an unattended ablation impossible. Memory ON/OFF is the only variable, so a
    no-approval task set keeps the comparison clean AND unattended.
    """
    items = [
        i
        for i in rc.load_items()
        if i.get("split") == "train"
        and i.get("target_tools")
        and all(t in rc._APPROVAL_FREE for t in i["target_tools"])
    ]
    if not items:
        return []
    rng = random.Random(seed)
    k = min(n, len(items))
    return sorted(it["id"] for it in rng.sample(items, k))


def summarize(records: list[dict]) -> dict:
    n = len(records)
    if not n:
        return {}
    return {
        "runs": n,
        "success_rate": round(sum(1 for r in records if r.get("verified")) / n, 3),
        "ineligible_rate": round(
            sum(1 for r in records if r.get("ineligible")) / n, 3
        ),
        "tool_hit_rate": round(sum(1 for r in records if r.get("tool_hit")) / n, 3),
        "cli_ok_rate": round(sum(1 for r in records if r.get("cli_ok")) / n, 3),
        "avg_tools": round(
            sum(len(r.get("tools_used") or []) for r in records) / n, 2
        ),
    }


def compare(on: list[dict], off: list[dict]) -> dict:
    so, sf = summarize(on), summarize(off)
    keys = ("success_rate", "tool_hit_rate", "cli_ok_rate", "avg_tools")
    delta = {
        k: round(so.get(k, 0) - sf.get(k, 0), 3)
        for k in keys
        if k in so and k in sf
    }
    return {"memory_on": so, "memory_off": sf, "delta_on_minus_off": delta}


def _path(arm: str) -> Path:
    return RESULTS / f"memory_ablation_{arm}.jsonl"


def _read(arm: str) -> list[dict]:
    p = _path(arm)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Memory ablation (Experiment B)")
    ap.add_argument("--arm", choices=["on", "off"])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()

    if args.compare:
        res = compare(_read("on"), _read("off"))
        RESULTS.mkdir(parents=True, exist_ok=True)
        atomic_write_json(RESULTS / "memory_ablation_compare.json", res)
        print(json.dumps(res, indent=2))
        return 0

    if not args.arm:
        ap.error("--arm {on,off} is required unless --compare")

    ids = fixed_ids(args.n, args.seed)
    items = [i for i in rc.load_items() if i["id"] in set(ids)]
    flag = "0" if args.arm == "off" else "1 (or unset)"
    print(
        f"ARM={args.arm}  n={len(items)} seed={args.seed}\n"
        f"  -> the BACKEND must be running with SWARM_MEMORY_INJECT={flag}\n"
        f"  -> (memory injection happens in the backend, not the CLI)"
    )
    # Headless run: no toast popups, and grant the offline tools so the agent's
    # sandbox_repl/lsp calls aren't prompted or denied — constant across BOTH arms.
    os.environ["SWARM_NO_TOASTS"] = "1"
    granted = False
    try:
        from swarm_os.services.trust_ledger import grant

        for t in ("sandbox_repl", "lsp", "git"):
            grant(t, 8 * 3600)
        granted = True
    except Exception:  # noqa: BLE001
        pass

    try:
        results = [
            rc.run_item(it, timeout=args.timeout, allow_approval=False) for it in items
        ]
    finally:
        if granted:
            try:
                from swarm_os.services.trust_ledger import revoke

                for t in ("sandbox_repl", "lsp", "git"):
                    revoke(t)
            except Exception:  # noqa: BLE001
                pass

    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(_path(args.arm), "w", encoding="utf-8") as fh:
        for r in results:
            json.dump(r, fh, ensure_ascii=False)
            fh.write("\n")
    print(f"  summary: {summarize(results)}")
    print(f"wrote -> {_path(args.arm).relative_to(_HERE.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
