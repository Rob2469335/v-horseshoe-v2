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
        # --- added 2026-09: distinct bug semantics (each needs a DIFFERENT fix) ---
        "wrong_index": (
            "def last(xs):\n    return xs[len(xs)]\n",
            "from module import last\nassert last([1, 2, 3]) == 3, last([1, 2, 3])\nassert last([]) is None\nprint('OK')\n",
        ),
        "wrong_comparison": (
            "def over(x, limit):\n    return x < limit\n",
            "from module import over\nassert over(5, 3) is True\nassert over(2, 3) is False\nprint('OK')\n",
        ),
        "wrong_boolean": (
            "def accept(a, b):\n    return a and b\n",
            "from module import accept\nassert accept(True, False) is True\nprint('OK')\n",
        ),
        "none_guard": (
            "def first_name(user):\n    return user['name']\n",
            "from module import first_name\nassert first_name({'name': 'Al'}) == 'Al'\nassert first_name({}) is None\nprint('OK')\n",
        ),
        "wrong_strip": (
            "def trim(s):\n    return s.lstrip()\n",
            "from module import trim\nassert trim('  hi  ') == 'hi', trim('  hi  ')\nprint('OK')\n",
        ),
        "wrong_join": (
            "def join(xs):\n    return ','.join(xs)\n",
            "from module import join\nassert join(['a', 'b']) == 'a, b', join(['a', 'b'])\nprint('OK')\n",
        ),
        "logic_inversion": (
            "def is_even(n):\n    return n % 2 == 1\n",
            "from module import is_even\nassert is_even(2) is True\nassert is_even(3) is False\nprint('OK')\n",
        ),
        "wrong_step": (
            "def evens(n):\n    return list(range(0, n, 1))\n",
            "from module import evens\nassert evens(10) == [0, 2, 4, 6, 8], evens(10)\nprint('OK')\n",
        ),
        "zero_div_guard": (
            "def divide(a, b):\n    return a / b\n",
            "from module import divide\nassert divide(6, 2) == 3.0\nassert divide(6, 0) is None\nprint('OK')\n",
        ),
        "wrong_cast": (
            "def normalize(x):\n    return str(x)\n",
            "from module import normalize\nassert normalize('42') == 42, normalize('42')\nprint('OK')\n",
        ),
        "fencepost": (
            "def count_up(n):\n    return len(range(1, n))\n",
            "from module import count_up\nassert count_up(5) == 5, count_up(5)\nprint('OK')\n",
        ),
        # --- expanded 2026-09-14: second batch (14 more distinct kinds) ---
        # COLLECTION: wrong slice endpoint (drops first instead of last)
        "wrong_slice": (
            "def drop_last(items):\n    return items[1:]\n",
            "from module import drop_last\nassert drop_last([1, 2, 3]) == [1, 2], drop_last([1, 2, 3])\nprint('OK')\n",
        ),
        # COLLECTION: empty-list crash (distinct from dict none_guard)
        "empty_collection_guard": (
            "def first(xs):\n    return xs[0]\n",
            "from module import first\nassert first([7]) == 7\nassert first([]) is None\nprint('OK')\n",
        ),
        # CONTRACT: mutable default argument — list shared across calls
        "mutable_default": (
            "def append_val(x, acc=[]):\n    acc.append(x)\n    return acc\n",
            "from module import append_val\nr1 = append_val(1)\nr2 = append_val(2)\nassert r1 == [1], r1\nassert r2 == [2], r2\nprint('OK')\n",
        ),
        # EXPRESSION_VALUE: operator precedence — addition before multiply
        "arithmetic_precedence": (
            "def scale_sum(a, b, c):\n    return a + b * c\n",
            "from module import scale_sum\nassert scale_sum(2, 3, 4) == 20, scale_sum(2, 3, 4)\nprint('OK')\n",
        ),
        # EXPRESSION_VALUE: type coercion — str concat with int raises TypeError
        "type_coercion": (
            "def label(count):\n    return 'count: ' + count\n",
            "from module import label\nassert label(3) == 'count: 3', label(3)\nprint('OK')\n",
        ),
        # EXPRESSION_VALUE: wrong numeric constant (100 vs 10)
        "wrong_constant": (
            "def ten_x(price):\n    return price * 100\n",
            "from module import ten_x\nassert ten_x(7) == 70, ten_x(7)\nprint('OK')\n",
        ),
        # CALL_API: wrong builtin called (max instead of min)
        "wrong_function_call": (
            "def minimum(values):\n    return max(values)\n",
            "from module import minimum\nassert minimum([3, 1, 5]) == 1, minimum([3, 1, 5])\nprint('OK')\n",
        ),
        # CALL_API: wrong argument order (positional args swapped)
        "wrong_argument_order": (
            "def left_pad(width, s):\n    return s.rjust(width)\n",
            "from module import left_pad\nassert left_pad('hi', 6) == '    hi', repr(left_pad('hi', 6))\nprint('OK')\n",
        ),
        # CALL_API: forgot parentheses — returns bound method, not result
        "missing_call": (
            "def uppercased(s):\n    return s.upper\n",
            "from module import uppercased\nassert uppercased('hi') == 'HI', uppercased('hi')\nprint('OK')\n",
        ),
        # CONTROL_FLOW: return vs print — prints but returns None
        "return_vs_print": (
            "def square(n):\n    print(n * n)\n",
            "from module import square\nassert square(4) == 16, square(4)\nprint('OK')\n",
        ),
        # STRING: missing normalisation before compare (strip + casefold)
        "string_normalization": (
            "def match(a, b):\n    return a == b\n",
            "from module import match\nassert match('  Hello ', 'hello') is True\nprint('OK')\n",
        ),
        # DATA_FLOW: identity vs equality — `is` fails on non-interned int
        "identity_vs_equality": (
            "def check_thousand(n):\n    return n is 1000\n",
            "from module import check_thousand\nassert check_thousand(1000) is True\nprint('OK')\n",
        ),
        # DATA_FLOW: loop over indices instead of values
        "wrong_loop_target": (
            "def total(nums):\n    s = 0\n    for i in range(len(nums)):\n        s += i\n    return s\n",
            "from module import total\nassert total([10, 20, 30]) == 60, total([10, 20, 30])\nprint('OK')\n",
        ),
        # DATA_FLOW: loop overwrites accumulator each iteration (never compares)
        "shadowed_variable": (
            "def running_max(nums):\n    best = nums[0]\n    for n in nums:\n        best = n\n    return best\n",
            "from module import running_max\nassert running_max([3, 1, 4, 1, 5]) == 5\nassert running_max([9, 2]) == 9\nprint('OK')\n",
        ),
        # DATA_FLOW: accumulator used before initialisation — NameError
        "uninitialized_use": (
            "def count_pos(nums):\n    for n in nums:\n        if n > 0:\n            c += 1\n    return c\n",
            "from module import count_pos\nassert count_pos([1, -2, 3]) == 2\nassert count_pos([-1]) == 0\nprint('OK')\n",
        ),
    }


_FAMILY = {
    "off_by_one": "arithmetic",
    "wrong_op": "arithmetic",
    "missing_return": "control_flow",
    "wrong_default": "contract",
    "string_case": "string",
    "wrong_index": "collection",
    "wrong_comparison": "logic",
    "wrong_boolean": "logic",
    "none_guard": "collection",
    "wrong_strip": "string",
    "wrong_join": "string",
    "logic_inversion": "logic",
    "wrong_step": "arithmetic",
    "zero_div_guard": "arithmetic",
    "wrong_cast": "expression_value",
    "fencepost": "arithmetic",
    "wrong_slice": "collection",
    "empty_collection_guard": "collection",
    "mutable_default": "contract",
    "arithmetic_precedence": "expression_value",
    "type_coercion": "expression_value",
    "wrong_constant": "expression_value",
    "wrong_function_call": "call_api",
    "wrong_argument_order": "call_api",
    "missing_call": "call_api",
    "return_vs_print": "control_flow",
    "string_normalization": "string",
    "identity_vs_equality": "data_flow",
    "wrong_loop_target": "data_flow",
    "shadowed_variable": "data_flow",
    "uninitialized_use": "data_flow",
}


def kind_family(kind: str) -> str:
    return _FAMILY.get(kind, "general")


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
        "family": kind_family(kind),
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
