"""Phase-2 READ-ONLY baseline: what does the CLI actually do on attempt #1?

This is the measurement gate the audits required before ANY curriculum or
weakness-model work. It answers one question:

    "When the CLI meets a task, what exactly does it get wrong first?"

It is strictly OBSERVATIONAL:
  - `record=False` on every attempt -> no tool-policy / learning write happens
  - no memory, no outcome_fitness, no reflexion rules, no prompts are modified
  - the only files it writes are its OWN sandbox fixtures + its results file

IMPORTANT (verified in code): the two isolation gates live in the BACKEND, not
the CLI — `SWARM_MEMORY_INJECT` is read by runtime_v2/services/stream_runner.py
and `SWARM_EVOLUTION` by runtime_v2/api/agent_service_v2.py. Both are loaded at
backend STARTUP, so setting them here is NOT enough: the backend must be
(re)started with `SWARM_MEMORY_INJECT=0` and `SWARM_EVOLUTION=0`. This script
records the exact launch context so "clean" is auditable, not asserted.

For a `fix_file` task the checker runs POST-run (run_curriculum.verify returns
None for those), so each attempt is verified here with the real checker
(run_candidate_pool._verify) rather than relying on run_item's internal verify.

Usage:
  python qwen_train/cli_baseline.py --n 20 --attempts 1
  python qwen_train/cli_baseline.py --pool qwen_train/curriculum/fix_pool_merged.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import run_candidate_pool as rcp  # noqa: E402
import run_curriculum as rc  # noqa: E402
from _atomic import atomic_write_json  # noqa: E402

ROOT = _HERE.parent
DEFAULT_POOL = _HERE / "curriculum" / "fix_pool_merged.jsonl"
OUT = _HERE / "results" / "cli_baseline.jsonl"

_record_lock = threading.Lock()


def _sha_file(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()[:12]
    except Exception:  # noqa: BLE001
        return ""


def experiment_context(pool: Path, attempts: int) -> dict:
    """Reproducibility block. `config_hash` = the EXPERIMENT config (not the
    repo — that is `cli_version`): prompt template + granted scopes + backend
    isolation flags. Without this the numbers are unreproducible."""
    cfg = {
        "prompt_template": "fix_file: patch ONLY the module so the checker exits 0",
        "granted_scopes": sorted(rc._GRANTABLE),
        "backend_flags": {
            "SWARM_MEMORY_INJECT": os.environ.get("SWARM_MEMORY_INJECT", "<unset>"),
            "SWARM_EVOLUTION": os.environ.get("SWARM_EVOLUTION", "<unset>"),
        },
        "attempts": attempts,
    }
    blob = json.dumps(cfg, sort_keys=True).encode("utf-8")
    return {
        "cli_version": _git_sha(),
        "pool": str(pool),
        "pool_sha256": _sha_file(pool),
        "config_hash": hashlib.sha256(blob).hexdigest()[:16],
        "config_hash_inputs": cfg,
    }


def _backend_up() -> bool:
    import urllib.request

    # `/readyz` first: it is the fast, meaningful probe (~4s). `/health` can take
    # 45s+ under memory pressure and would make a live backend look dead.
    for path in ("/readyz", "/health"):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:8000{path}", timeout=5
            ) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def run_task(i: int, cand: dict, attempts: int, timeout: int) -> dict:
    """Measure ONE candidate. Read-only: run_item/record are never used to
    write learning state; verification is the real checker per attempt."""
    item = rcp._make_task(cand, i)
    module = ROOT / item["verify"]["module"]
    check_rel = item["verify"]["check"]

    def _reset() -> None:
        # Rewrite the BROKEN module so every attempt starts identical: without
        # this, attempt 2 would measure a different task than attempt 1.
        rcp._make_task(cand, i)

    attempt_rows: list[dict] = []
    to_success: int | None = None
    for n in range(1, attempts + 1):
        if n > 1:
            _reset()
        before = _sha_file(module)
        res = rc._attempt_once(item, timeout, allow_approval=False, record=False)
        after = _sha_file(module)
        ok, reason = rcp._verify(item)
        res["verified"] = ok
        res["verify_reason"] = reason
        res["module_changed"] = before != after
        cat = rc.classify_failure(
            res, module_changed=res["module_changed"], check_output=reason
        )
        res["failure_category"] = cat
        res["attempt"] = n
        attempt_rows.append(res)
        if ok and to_success is None:
            to_success = n
        if res.get("timed_out"):
            break

    first = attempt_rows[0]
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task_id": item["id"],
        "kind": item.get("kind", ""),
        "family": item.get("family_tax", ""),
        "check": check_rel,
        # ---- the headline datum: ATTEMPT 1 ----
        "first_attempt_success": bool(first.get("verified")),
        "first_failure_category": first.get("failure_category"),
        "first_tool_order": first.get("tool_order", []),
        "first_elapsed_s": first.get("elapsed_s"),
        # ---- recovery (only meaningful when attempts>1) ----
        "attempts_run": len(attempt_rows),
        "attempts_to_success": to_success,
        "recovered": bool(to_success and to_success > 1),
        "final_success": bool(attempt_rows[-1].get("verified")),
        "categories": [a.get("failure_category") for a in attempt_rows],
        "module_changed": first.get("module_changed"),
        "denied": first.get("denied", []),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only CLI baseline (Phase 2)")
    ap.add_argument("--pool", default=str(DEFAULT_POOL))
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="1 = pure first-attempt baseline (the datum). >1 also measures recovery.",
    )
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    # Read-only posture: hard-set the CLI-side gates (the BACKEND must also be
    # started with them — see the module docstring).
    os.environ["SWARM_NO_TOASTS"] = "1"
    os.environ["SWARM_MEMORY_INJECT"] = "0"
    os.environ["SWARM_EVOLUTION"] = "0"

    pool = Path(args.pool)
    if not pool.exists():
        print(f"pool not found: {pool}")
        return 2
    cands = [
        json.loads(line)
        for line in pool.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ][: args.n]

    if not _backend_up():
        print("BACKEND DOWN at 127.0.0.1:8000 — the CLI cannot run. Start the stack")
        print("(and start it with SWARM_MEMORY_INJECT=0 SWARM_EVOLUTION=0).")
        return 2

    ctx = experiment_context(pool, args.attempts)
    print(
        f"baseline: {len(cands)} tasks, attempts={args.attempts}, timeout={args.timeout}s\n"
        f"  cli_version={ctx['cli_version']}  pool_sha={ctx['pool_sha256']}\n"
        f"  backend flags seen here: {ctx['config_hash_inputs']['backend_flags']}"
    )

    # A scope-limited grant so the coder's write/sandbox tools run unattended.
    # ONE grant for the whole run (grants are TTL-scoped, not per-call); revoked once.
    granted = False
    try:
        from swarm_os.services.trust_ledger import grant

        for scope in ("filesystem", "sandbox_repl"):
            grant(scope, 12 * 3600)
        granted = True
    except Exception as exc:  # noqa: BLE001
        print(f"grant failed: {exc}")

    rows: list[dict] = []
    try:
        for i, cand in enumerate(cands):
            try:
                row = run_task(i, cand, args.attempts, args.timeout)
            except Exception as exc:  # noqa: BLE001
                row = {
                    "task_id": f"cand{i:03d}",
                    "first_attempt_success": None,
                    "first_failure_category": "instrument_error",
                    "error": str(exc)[:200],
                }
            with _record_lock:
                rows.append(row)
                with open(args.out, "a", encoding="utf-8") as fh:  # append, never truncates
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            mark = "PASS" if row.get("first_attempt_success") else "FAIL"
            print(
                f"[{i + 1}/{len(cands)}] {row.get('task_id')} {mark} "
                f"{row.get('first_failure_category')}",
                flush=True,
            )
    finally:
        if granted:
            try:
                from swarm_os.services.trust_ledger import revoke

                for scope in ("filesystem", "sandbox_repl"):
                    revoke(scope)
            except Exception:  # noqa: BLE001
                pass

    measured = [r for r in rows if r.get("first_attempt_success") is not None]
    passes = sum(1 for r in measured if r["first_attempt_success"])
    cats: dict[str, int] = {}
    for r in measured:
        cats[r.get("first_failure_category") or "?"] = (
            cats.get(r.get("first_failure_category") or "?", 0) + 1
        )
    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "context": ctx,
        "n": len(rows),
        "measured": len(measured),
        "first_attempt_passes": passes,
        "first_attempt_success_rate": round(passes / max(len(measured), 1), 3),
        "failure_categories": cats,
        "note": (
            "rate ~1.0 => pool is ABOVE the learning band (no gradient); "
            "rate ~0.0 => below it (no signal); 0.3-0.8 is where learning happens"
        ),
    }
    atomic_write_json(Path(args.out).with_suffix(".summary.json"), summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
