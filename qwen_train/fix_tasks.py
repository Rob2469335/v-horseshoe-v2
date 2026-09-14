"""Write/FIX task family — the missing capability family.

The whole curriculum is read/compute/git; nothing exercises editing a file. This is
the north-star family ("fix a real bug"), and the most valuable GOLD for Experiment C.

Safety model (three required parts):
1. SCRATCH SANDBOX   each task lives in gitignored `data/curriculum_fix/<id>/`.
2. PATH-SCOPED WRITE the agent must only edit the task module — enforced by the
   `SWARM_WRITE_ROOT` root restriction in the filesystem handler (see
   docs/WRITE_FIX_TASKS.md; the current gate is tool-level, so this is the gating
   enhancement). Without it, a filesystem write grant would open the whole repo.
3. POST-RUN VERIFY   after the run, re-read the file and run its `check.py`
   (subprocess, exit 0) AND confirm `check.py` is byte-unchanged (checked via sha256).

The bug set is deterministic, tiny, and single-fix, so the verifier is exact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
SANDBOX = ROOT / "data" / "curriculum_fix"


def _tasks() -> dict:
    return {
        "off_by_one": (
            "def total(n):\n    return sum(range(1, n))\n",
            "from module import total\nassert total(4) == 10, total(4)\nprint('OK')\n",
        ),
        "wrong_op": (
            "def add(a, b):\n    return a - b\n",
            "from module import add\nassert add(3, 4) == 7, add(3, 4)\nprint('OK')\n",
        ),
        "missing_return": (
            "def double(x):\n    x * 2\n",
            "from module import double\nassert double(5) == 10, double(5)\nprint('OK')\n",
        ),
        "wrong_default": (
            "def scale(x, factor=2):\n    return x * factor\n",
            "from module import scale\nassert scale(3) == 9, scale(3)\nprint('OK')\n",
        ),
        "string_case": (
            "def shout(s):\n    return s.lower()\n",
            "from module import shout\nassert shout('hi') == 'HI', shout('hi')\nprint('OK')\n",
        ),
    }


def make_task(kind: str, idx: int, root: Path = SANDBOX, repo_root: Path = ROOT) -> dict:
    """Write a broken module + its check into the sandbox; return the item."""
    module_src, check_src = _tasks()[kind]
    d = root / f"{kind}_{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "module.py").write_text(module_src, encoding="utf-8")
    (d / "check.py").write_text(check_src, encoding="utf-8")
    check_sha = hashlib.sha256(check_src.encode("utf-8")).hexdigest()[:16]
    rel = str((d / "module.py").relative_to(repo_root)).replace("\\", "/")
    check_rel = str((d / "check.py").relative_to(repo_root)).replace("\\", "/")
    return {
        "id": f"f{kind}{idx:03d}",
        "split": "train",
        "difficulty": 3,
        "target_tools": ["filesystem", "sandbox_repl"],
        "prompt": (
            f"Fix the file `{rel}` so that running `python {check_rel}` exits 0. "
            f"Read the check to see the expected behaviour, then patch ONLY the module. "
            f"Do NOT modify the check."
        ),
        "verify": {
            "type": "fix_file",
            "root": str(repo_root),
            "module": rel,
            "check": check_rel,
            "check_sha256": check_sha,
        },
    }


def verify_fix(item: dict, timeout: int = 60) -> dict:
    """Post-run verifier: check.py unchanged AND exits 0."""
    spec = item.get("verify") or {}
    base = Path(spec.get("root", ROOT))
    module_p = base / spec.get("module", "")
    check_p = base / spec.get("check", "")
    if not check_p.exists() or not module_p.exists():
        return {"passed": False, "reason": "module/check missing"}
    cur = hashlib.sha256(check_p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()[:16]
    if cur != spec.get("check_sha256"):
        return {"passed": False, "reason": "check.py was modified (forbidden)"}
    try:
        proc = subprocess.run(
            [sys.executable, check_p.name],
            cwd=str(check_p.parent),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "reason": "check timed out"}
    ok = proc.returncode == 0
    return {"passed": ok, "reason": (proc.stdout or proc.stderr or "").strip()[:200]}


def generate(n_per_kind: int = 3, root: Path = SANDBOX, repo_root: Path = ROOT) -> list[dict]:
    items = []
    for kind in _tasks():
        for i in range(n_per_kind):
            items.append(make_task(kind, i, root, repo_root))
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate write/fix tasks")
    ap.add_argument("--gen", type=int, default=3, help="tasks per bug kind")
    ap.add_argument("--verify", help="verify a task id (re-runs its check)")
    args = ap.parse_args()

    if args.verify:
        items = generate(3)
        item = next((i for i in items if i["id"] == args.verify), None)
        if not item:
            print(f"unknown task id {args.verify}")
            return 1
        print(json.dumps(verify_fix(item), indent=2))
        return 0

    items = generate(args.gen)
    print(f"generated {len(items)} fix tasks under {SANDBOX.relative_to(ROOT)}")
    print("(broken by construction: verify_fix() is False until the module is patched)")
    print("preview:", json.dumps(items[0], indent=2)[:600])
    return 0


if __name__ == "__main__":
    sys.exit(main())
