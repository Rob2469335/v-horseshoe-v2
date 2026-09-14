"""Write/fix task runner (docs/WRITE_FIX_TASKS.md).

Fix tasks are STATEFUL — the module must be re-broken before every run — so they
can't live in the read-only pool. This harness: (re)writes the broken module →
runs the CLI → verifies POST-RUN (check exits 0 AND check.py unchanged) → records.

REQUIRES the BACKEND to be started with SWARM_WRITE_ROOT=<sandbox dir>, so the
agent's filesystem writes are confined to the task sandbox (the scoped filesystem
grant minted below would otherwise open the whole repo). Set it in .env and
restart the backend before running this. The env is read by the BACKEND handler,
not here.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import fix_tasks as ft  # noqa: E402
import run_curriculum as rc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Write/fix task runner")
    ap.add_argument("--n-per-kind", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    os.environ["SWARM_NO_TOASTS"] = "1"
    wr = os.environ.get("SWARM_WRITE_ROOT", "")
    print(
        f"SWARM_WRITE_ROOT(local)={wr!r}\n"
        "  -> the BACKEND must ALSO be started with SWARM_WRITE_ROOT pointing at the "
        "sandbox, or writes are blocked/open.\n"
        f"  -> sandbox: {ft.SANDBOX.relative_to(ft.ROOT)}"
    )

    granted = False
    try:
        from swarm_os.services.trust_ledger import grant

        grant("filesystem", 8 * 3600)
        granted = True
    except Exception as exc:  # noqa: BLE001
        print(f"filesystem grant failed: {exc}")

    try:
        for kind in ft._tasks():
            for i in range(args.n_per_kind):
                item = ft.make_task(kind, i)  # (re)writes the BROKEN module
                print(f"[{item['id']}] {item['prompt'][:90]}")
                res = rc.run_item(item, timeout=args.timeout, allow_approval=False)
                fx = ft.verify_fix(item)
                res["verified"] = fx["passed"]
                res["verify_reason"] = fx["reason"]
                res["family"] = "fix"
                rc.record(res)
                mark = {True: "PASS", False: "FAIL", None: "MANUAL"}[fx["passed"]]
                print(f"  fix={mark} tools={res['tools_used']} ({fx['reason'][:70]})")
    finally:
        if granted:
            try:
                from swarm_os.services.trust_ledger import revoke

                revoke("filesystem")
            except Exception:  # noqa: BLE001
                pass

    rc.progress()
    return 0


if __name__ == "__main__":
    sys.exit(main())
