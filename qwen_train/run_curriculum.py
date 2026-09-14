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
import ast
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
HOLDOUT = _HERE / "curriculum" / "holdout.jsonl"  # frozen eval split (never trained on)
RESULTS = _HERE / "results" / "curriculum_runs.jsonl"

_TARGET_RUNS = 500
_TOOL_RE = re.compile(r"[⚡✓▶]\s+([a-z_][a-z0-9_]*)")

# Tools that need NO human approval (approval_registry ALLOW tier), so a batch
# run can use them unattended without prompts. sandbox_repl/git/system/mcp are
# CONFIRM/ALWAYS_CONFIRM and would hang a non-interactive run (or pop up
# "approval required"). Only filesystem reads / web_search / semantic_search
# are safe here.
_APPROVAL_FREE = {"filesystem", "web_search", "semantic_search"}

# Tools the runner grants for an offline run: task-scoped, audited, revoked at
# the end. `sandbox_repl` is ALWAYS_CONFIRM (relaxed only via
# approval_registry._OFFLINE_GRANTABLE); `lsp` is CONFIRM (tightened by the same
# scoped trust_ledger grant). Dynamic least-privilege + task-scoped grants
# (arXiv:2607.22445; 2603.17170) — never blanket auto-approve.
_GRANTABLE = ("sandbox_repl", "lsp", "git")

# A tool the CLI auto-DENIED (non-interactive fail-closed, or explicit policy).
# A run whose intended tool was denied is INELIGIBLE for the learning signal,
# not a failure (arXiv:2604.11839; ACP rollout: mark ineligible, don't score).
_DENY_RE = re.compile(
    r"(?:non-interactive:\s*denied|auto-denied:)\s*([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)


def parse_tools_denied(stdout: str) -> list[str]:
    """Tool names the CLI refused (auto-denied) during the run."""
    return sorted(set(_DENY_RE.findall(stdout or "")))


def _grant_offline() -> str:
    """Grant the task-scoped offline tool set (audited, expiring, revoked after)."""
    try:
        from swarm_os.services.trust_ledger import grant

        for tool in _GRANTABLE:
            grant(tool, 8 * 3600)
        return f"granted {', '.join(_GRANTABLE)} (scoped, 8h, audited)"
    except Exception as exc:  # noqa: BLE001
        return f"grant failed: {exc}"


def _revoke_offline() -> None:
    try:
        from swarm_os.services.trust_ledger import revoke

        for tool in _GRANTABLE:
            revoke(tool)
    except Exception:  # noqa: BLE001
        pass


def load_items() -> list[dict]:
    items: list[dict] = []
    for path in (CURRICULUM, GENERATED, HOLDOUT):
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

        def _hit(v: str) -> bool:
            # Anti-reward-hacking (AlphaVerus/AxDafny 2026): a numeric expected
            # value must match as a STANDALONE token, so "12" cannot be matched
            # inside "3912". Non-numeric values stay substring matches.
            if v.isdigit():
                import re as _re

                return bool(_re.search(rf"(?<!\d){_re.escape(v)}(?!\d)", text))
            return v in text

        hits = [v for v in vals if _hit(v)]
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


_TOOL_OK_RE = re.compile(r"✓\s+([a-z_][a-z0-9_]*)")


def parse_tools_succeeded(stdout: str) -> list[str]:
    """Tool names whose call RETURNED successfully (the ✓ stream marker)."""
    return sorted(set(_TOOL_OK_RE.findall(stdout or "")))


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


def run_item(item: dict, timeout: int = 600, allow_approval: bool = False) -> dict:
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
            # ALWAYS_CONFIRM tools (sandbox_repl, …) prompt the CLI; a pipe of
            # "y" answers them so an opted-in unattended run can proceed.
            input=("y\n" * 50) if allow_approval else "",
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        out = ""
    cli = extract_result(out)
    content = (cli or {}).get("content", "")
    used = parse_tools_used(out)
    succeeded = parse_tools_succeeded(out)
    denied = parse_tools_denied(out)
    check = verify(item, content)
    cli_ok = bool((cli or {}).get("ok"))
    # INELIGIBLE: the intended tool was auto-DENIED (not a failure). Excluded
    # from the learning signal, not scored (ACP rollout / arXiv:2604.11839).
    ineligible = bool(set(item.get("target_tools") or []) & set(denied))
    # Did the INTENDED tool actually succeed? If it ran fine but the answer
    # failed verification, the failure is downstream (answer synthesis) and
    # must NOT be recorded as a tool failure (false correlation; see the audit).
    tool_ok = bool(set(item.get("target_tools") or []) & set(succeeded))
    if cli_ok and not ineligible:
        try:
            from runtime_v2.services.tool_policy import record_observation

            record_observation(prompt, used, check.get("passed"), tool_ok=tool_ok)
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
        "ineligible": ineligible,
        "denied": denied,
        # Reasoning-data taxonomy (Awesome-LLM-Reasoning-Data): who checks the
        # answer, at what granularity, and which objective consumes it.
        "verifier": (item.get("verify") or {}).get("type", "contains"),
        "granularity": "trajectory",
        "consumer": "policy",
        # RECOVERY = a run that used tools beyond the intended set and STILL
        # succeeded (recoverable trajectory worth mining; VPR/TRACE step credit).
        "recovery": bool(check.get("passed"))
        and bool(set(used) - set(item.get("target_tools") or [])),
        "content": str(content)[:600],
        "elapsed_s": round(elapsed, 1),
    }


# --------------------------------------------------------------------------
# verified variant generator — scale to 500 with REAL diversity, not repeats
# --------------------------------------------------------------------------

_PI_CACHE: dict[int, int] = {}

# Grounded file lookups (filesystem family) — answers verified against the repo.
_LOOKUPS = [
    (
        "Read runtime_v2/api/_agent_config.py and report the integer value of MAX_TURNS.",
        "12",
    ),
    (
        "Read runtime_v2/api/_agent_config.py and report the integer value of MAX_DEPTH.",
        "15",
    ),
    (
        "Read runtime_v2/api/_agent_config.py and report the integer value of MAX_RESULT_CHARS.",
        "1200",
    ),
    (
        "Read runtime_v2/api/_agent_config.py and report the integer value of MAX_HISTORY_TURNS.",
        "4",
    ),
    (
        "Read runtime_v2/services/stream_runner.py and report the integer value of _NOTICE_RESERVE.",
        "160",
    ),
    (
        "Read organism_console/state_store.py and report the integer value of _SESSION_MAX_MESSAGES.",
        "60",
    ),
    ("Read pyproject.toml and report the exact value of requires-python.", "3.14"),
    (
        "Read runtime_v2/services/model_registry.py and report the model name all built-in agents map to.",
        "robs4b",
    ),
]


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
    # 50/50 across two tool families so a large N stays DIVERSE: `filesystem`
    # (grounded file lookups) and `sandbox_repl` (computed math). Cranking only
    # one family would skew the learned tool policy toward that one tool.
    if rng.random() < 0.5:
        prompt, answer = rng.choice(_LOOKUPS)
        return {
            "id": f"g{idx:04d}",
            "split": "train",
            "difficulty": 1,
            "target_tools": ["filesystem"],
            "prompt": prompt,
            "verify": {"type": "contains", "mode": "all", "value": [answer]},
        }
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
# repo-grounded task mining (DIVE arXiv:2603.11076: ground tasks in REAL data)
# --------------------------------------------------------------------------

_CONST_RE = re.compile(r"^([A-Z][A-Z0-9_]{3,})\s*=\s*(.+?)\s*$")
_DEF_RE = re.compile(r"^(?:def|class)\s+(\w+)")
_BORING = {"0", "1", "2", "3", "4", "5", "10", "100", "0.0", "1.0", "True", "False"}


def _repo_files(root: Path, limit: int = 220) -> list[Path]:
    files: set[Path] = set()
    for pat in ("runtime_v2/**/*.py", "swarm_os/**/*.py", "organism_console/**/*.py"):
        files.update(p for p in root.glob(pat) if p.is_file())
    return sorted(files)[:limit]


def _literal_expected(expr: str) -> str | None:
    try:
        val = ast.literal_eval(expr)
    except ValueError, SyntaxError:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        s = str(val)
        return s if len(s) >= 2 and s not in _BORING else None
    if isinstance(val, str):
        v = val.strip()
        return v if 2 <= len(v) <= 40 else None
    return None


def _const_items(root: Path, per_file: int = 3) -> list[dict]:
    out: list[dict] = []
    for p in _repo_files(root):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        picked = 0
        for line in text.splitlines():
            m = _CONST_RE.match(line)
            if not m:
                continue
            name, expr = m.group(1), m.group(2)
            exp = _literal_expected(expr)
            if not exp:
                continue
            out.append(
                {
                    "id": f"m{len(out):05d}",
                    "split": "train",
                    "difficulty": 1,
                    "target_tools": ["filesystem"],
                    "prompt": f"In {rel}, find and report the exact value of the {name} constant.",
                    "verify": {"type": "contains", "mode": "all", "value": [exp]},
                }
            )
            picked += 1
            if picked >= per_file:
                break
    return out


def _defcount_items(root: Path) -> list[dict]:
    out: list[dict] = []
    for p in _repo_files(root):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        n = sum(1 for line in text.splitlines() if _DEF_RE.match(line))
        if n < 1:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        out.append(
            {
                "id": f"n{len(out):05d}",
                "split": "train",
                "difficulty": 2,
                "target_tools": ["filesystem"],
                "prompt": (
                    f"Read {rel} and report how many top-level `def` and `class` "
                    "definitions it contains, as a number."
                ),
                "verify": {"type": "contains", "mode": "all", "value": [str(n)]},
            }
        )
    return out


def _exists_items(root: Path, limit: int = 60) -> list[dict]:
    out: list[dict] = []
    real = [
        str(p.relative_to(root)).replace("\\", "/") for p in _repo_files(root)[:limit]
    ]
    fake = [
        "runtime_v2/services/nonexistent_module_zz.py",
        "swarm_os/services/ghost_service_qq.py",
        "organism_console/_commands_imaginary.py",
        "docs/does_not_exist_here.md",
    ]
    for rel in real:
        out.append(
            {
                "id": f"e{len(out):05d}",
                "split": "train",
                "difficulty": 1,
                "target_tools": ["filesystem"],
                "prompt": f"Does the file {rel} exist in this repository? Answer yes or no.",
                "verify": {
                    "type": "contains",
                    "mode": "any",
                    "value": ["yes", "exists"],
                },
            }
        )
    for rel in fake:
        out.append(
            {
                "id": f"e{len(out):05d}",
                "split": "train",
                "difficulty": 1,
                "target_tools": ["filesystem"],
                "prompt": f"Does the file {rel} exist in this repository? Answer yes or no.",
                "verify": {
                    "type": "contains",
                    "mode": "any",
                    "value": ["no", "not exist", "doesn't", "does not"],
                },
            }
        )
    return out


def _math_pool(n: int = 900, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    out: list[dict] = []
    for i in range(n):
        fam = rng.choice(
            [
                "mul",
                "sum",
                "primes",
                "fib",
                "gcd",
                "divisors",
                "fact",
                "pow",
                "mod",
                "mean",
            ]
        )
        if fam == "mul":
            a, b = rng.randint(12, 999), rng.randint(12, 999)
            prompt, ans = f"compute {a} * {b}", a * b
        elif fam == "sum":
            k = rng.randint(20, 500)
            prompt, ans = (
                f"report the sum of all integers from 1 to {k}",
                k * (k + 1) // 2,
            )
        elif fam == "primes":
            k = rng.randint(30, 300)
            prompt, ans = (
                f"count how many prime numbers are below {k}",
                _primes_below(k),
            )
        elif fam == "fib":
            k = rng.randint(8, 22)
            prompt, ans = (
                f"compute the {k}th Fibonacci number where F(1)=1 and F(2)=1",
                _fib(k),
            )
        elif fam == "gcd":
            a, b = rng.randint(100, 99999), rng.randint(100, 99999)
            prompt, ans = (
                f"compute the greatest common divisor of {a} and {b}",
                math.gcd(a, b),
            )
        elif fam == "divisors":
            k = rng.randint(50, 5000)
            prompt, ans = (
                f"count how many divisors {k} has",
                sum(1 for d in range(1, k + 1) if k % d == 0),
            )
        elif fam == "fact":
            k = rng.randint(5, 12)
            prompt, ans = f"compute {k} factorial", math.factorial(k)
        elif fam == "pow":
            a, b = rng.randint(2, 9), rng.randint(3, 12)
            prompt, ans = f"compute {a} to the power of {b}", a**b
        elif fam == "mod":
            a, b = rng.randint(1000, 99999), rng.randint(7, 97)
            prompt, ans = f"compute the remainder of {a} divided by {b}", a % b
        else:
            k = rng.randint(3, 10)
            nums = [rng.randint(1, 200) for _ in range(k)]
            prompt, ans = f"compute the integer mean of the list {nums}", sum(nums) // k
        out.append(
            {
                "id": f"x{i:05d}",
                "split": "train",
                "difficulty": 1 if fam in ("mul", "sum") else 2,
                "target_tools": ["sandbox_repl"],
                "prompt": f"Use sandbox_repl to {prompt} and report the result as a number.",
                "verify": {"type": "contains", "mode": "all", "value": [str(ans)]},
            }
        )
    return out


def _string_pool(n: int = 300, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    words = [
        "agent",
        "swarm",
        "horseshoe",
        "python",
        "context",
        "reflexion",
        "harness",
        "kernel",
        "memory",
        "policy",
        "router",
        "checkpoint",
        "sandbox",
        "curriculum",
        "fitness",
        "canary",
        "genome",
        "telemetry",
    ]
    out: list[dict] = []
    for i in range(n):
        w = rng.choice(words) + rng.choice(["s", "", "ing", "ed"])
        op = rng.choice(["reverse", "length", "uppercase", "vowels"])
        if op == "reverse":
            prompt, ans = f"compute the reverse of the string '{w}'", w[::-1]
        elif op == "length":
            prompt, ans = f"report the number of characters in the string '{w}'", len(w)
        elif op == "uppercase":
            prompt, ans = f"compute the uppercase of the string '{w}'", w.upper()
        else:
            prompt, ans = (
                f"count the vowels in the string '{w}'",
                sum(c in "aeiou" for c in w),
            )
        out.append(
            {
                "id": f"s{i:05d}",
                "split": "train",
                "difficulty": 1,
                "target_tools": ["sandbox_repl"],
                "prompt": f"Use sandbox_repl to {prompt}, and report the result.",
                "verify": {"type": "contains", "mode": "all", "value": [str(ans)]},
            }
        )
    return out


def _symbol_items(root: Path, per_file: int = 2) -> list[dict]:
    """Symbol-presence (grounded): real defs -> yes, fabricated -> no."""
    out: list[dict] = []
    for p in _repo_files(root):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        names = [m.group(1) for line in text.splitlines() if (m := _DEF_RE.match(line))]
        for name in names[:per_file]:
            out.append(
                {
                    "id": f"y{len(out):05d}",
                    "split": "train",
                    "difficulty": 2,
                    "target_tools": ["filesystem"],
                    "prompt": (
                        f"Does {rel} define a function or class named `{name}`? "
                        "Answer yes or no."
                    ),
                    "verify": {"type": "contains", "mode": "any", "value": ["yes"]},
                }
            )
    for i in range(40):
        out.append(
            {
                "id": f"y{len(out):05d}",
                "split": "train",
                "difficulty": 2,
                "target_tools": ["filesystem"],
                "prompt": (
                    f"Does runtime_v2/api/_agent_config.py define a function or class "
                    f"named `totally_missing_symbol_{i}`? Answer yes or no."
                ),
                "verify": {"type": "contains", "mode": "any", "value": ["no", "not"]},
            }
        )
    return out


def _linecount_items(root: Path, limit: int = 120) -> list[dict]:
    out: list[dict] = []
    for p in _repo_files(root)[:limit]:
        try:
            n = len(p.read_text(encoding="utf-8").splitlines())
        except OSError:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        out.append(
            {
                "id": f"l{len(out):05d}",
                "split": "train",
                "difficulty": 1,
                "target_tools": ["filesystem"],
                "prompt": f"Read {rel} and report how many lines it has, as a number.",
                "verify": {"type": "contains", "mode": "all", "value": [str(n)]},
            }
        )
    return out


def _env_items() -> list[dict]:
    """A few non-filesystem probes for tool breadth (stable answers)."""
    return [
        {
            "id": "v00000",
            "split": "train",
            "difficulty": 1,
            "target_tools": ["git"],
            "prompt": "Use the git tool to report the current branch name.",
            "verify": {"type": "contains", "mode": "any", "value": ["master", "main"]},
        },
        {
            "id": "v00001",
            "split": "train",
            "difficulty": 2,
            "target_tools": ["system"],
            "prompt": "Use the system tool to report the operating system platform of this machine.",
            "verify": {"type": "contains", "mode": "any", "value": ["win", "windows"]},
        },
        {
            "id": "v00002",
            "split": "train",
            "difficulty": 1,
            "target_tools": ["filesystem"],
            "prompt": "Read pyproject.toml and report the exact value of requires-python.",
            "verify": {"type": "contains", "mode": "all", "value": ["3.14"]},
        },
    ]


def _git_items(root: Path, limit: int = 250) -> list[dict]:
    """Git-history tasks, HASH-ANCHORED so they stay verifiable (a commit's
    subject never changes). Real tool diversity: the `git` tool (granted for
    run #2). Answers are checked against the commit subject."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--pretty=format:%H|%s", "-n", str(limit)],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except Exception:  # noqa: BLE001
        return []
    items: list[dict] = []
    for line in out.splitlines():
        if "|" not in line:
            continue
        h, subj = line.split("|", 1)
        subj = subj.strip()
        if len(subj) < 8:
            continue
        items.append(
            {
                "id": f"t{len(items):05d}",
                "split": "train",
                "difficulty": 2,
                "target_tools": ["git"],
                "prompt": (
                    f"Use the git tool to report the SUBJECT LINE of the commit "
                    f"with hash {h}. Answer with the subject text only."
                ),
                "verify": {"type": "contains", "mode": "all", "value": [subj[:60]]},
            }
        )
    return items


def _symbol_loc_items(root: Path, limit: int = 300) -> list[dict]:
    """'Which file defines `<symbol>`?' — Qdrant/codebase-index-friendly diversity
    with an EXACT verifier: the symbol is chosen so it is defined in exactly one
    file with a unique basename, so the answer (the file) is unambiguous.

    Tool CHOICE: the agent may solve it via `filesystem` (grep/glob),
    `semantic_search` (codebase index), or `lsp` (rob's LSP — the same symbol
    surface Serena exposes over MCP). Serena itself is reachable via the `mcp`
    action, which is deliberately NOT in `_GRANTABLE` (broad capability, not
    least-privilege) — so a run that reaches for `mcp` is marked `ineligible`,
    not scored. `lsp` IS granted for run #2."""
    files = _repo_files(root)
    defs: dict[str, set[str]] = {}
    basenames: dict[str, set[str]] = {}
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        basenames.setdefault(p.name, set()).add(rel)
        for line in text.splitlines():
            m = _DEF_RE.match(line)
            if m:
                defs.setdefault(m.group(1), set()).add(rel)
    out: list[dict] = []
    for name, define_files in sorted(defs.items()):
        if len(define_files) != 1 or len(name) < 5:
            continue
        rel = next(iter(define_files))
        base = rel.rsplit("/", 1)[-1]
        if len(basenames.get(base, ())) != 1:
            continue  # unique basename -> an unambiguous answer
        out.append(
            {
                "id": f"d{len(out):05d}",
                "split": "train",
                "difficulty": 3,
                "target_tools": ["filesystem", "lsp", "semantic_search"],
                "prompt": (
                    f"Which file in this repository defines the function or class "
                    f"`{name}`? Report the file path."
                ),
                "verify": {"type": "contains", "mode": "all", "value": [base]},
            }
        )
        if len(out) >= limit:
            break
    return out


def mine_pool(root: Path | None = None) -> list[dict]:
    """Assemble the full diverse verified pool (repo-grounded + procedural)."""
    root = root or _HERE.parent
    pool: list[dict] = []
    pool += _const_items(root)
    pool += _defcount_items(root)
    pool += _exists_items(root)
    pool += _symbol_items(root)
    pool += _symbol_loc_items(root)
    pool += _linecount_items(root)
    pool += _env_items()
    pool += _git_items(root)
    pool += _math_pool()
    pool += _string_pool()
    return pool


def mine(n: int, seed: int = 0) -> int:
    """Sample N diverse verified items into the generated pool. Returns count."""
    pool = mine_pool()
    if not pool:
        return 0
    rng = random.Random(seed)
    rng.shuffle(pool)
    start = sum(1 for i in load_items() if str(i.get("id", "")).startswith("g"))
    written = 0
    GENERATED.parent.mkdir(parents=True, exist_ok=True)
    with GENERATED.open("a", encoding="utf-8") as fh:
        for item in pool:
            if written >= n:
                break
            item = {**item, "id": f"g{start + written:05d}"}
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            written += 1
    return written


def make_holdout(n: int = 50, seed: int = 99) -> int:
    """Write N FRESH `eval`-split items to the frozen holdout (disjoint instances
    from the training pool). `--run`/`next_item('train')` never select them; an
    eval run uses `--split eval`."""
    pool = mine_pool()
    rng = random.Random(seed)
    rng.shuffle(pool)
    start = sum(1 for i in load_items() if str(i.get("id", "")).startswith("h"))
    written = 0
    HOLDOUT.parent.mkdir(parents=True, exist_ok=True)
    with HOLDOUT.open("a", encoding="utf-8") as fh:
        for item in pool:
            if written >= n:
                break
            fh.write(
                json.dumps(
                    {**item, "id": f"h{start + written:04d}", "split": "eval"},
                    ensure_ascii=False,
                )
                + "\n"
            )
            written += 1
    return written


def coverage() -> dict:
    """Diversity snapshot over all curriculum items (the scaling metric)."""
    items = load_items()
    by_tool: dict[str, int] = {}
    by_shape: dict[str, int] = {}
    for it in items:
        for t in it.get("target_tools", []) or ["?"]:
            by_tool[t] = by_tool.get(t, 0) + 1
        by_shape[shape_of(it["prompt"])] = by_shape.get(shape_of(it["prompt"]), 0) + 1
    return {"items": len(items), "by_tool": by_tool, "by_shape": by_shape}


def shape_of(task: str) -> str:
    try:
        from runtime_v2.services.tool_policy import shape_of as _s

        return _s(task)
    except Exception:  # noqa: BLE001
        return "other"


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


def next_item(split: str = "all", approval_free: bool = True) -> dict | None:
    """The next item not yet run (a DIFFERENT one each time), wrapping when done.

    With ``approval_free`` (the default) only items whose tools are all in the
    ALLOW tier are candidates — so an unattended run never triggers an approval
    prompt. Pass False (with ``--allow-approval``) to include the rest.
    """
    items = load_items()
    if split != "all":
        items = [i for i in items if i.get("split") == split]
    if approval_free:
        items = [i for i in items if set(i.get("target_tools") or []) <= _APPROVAL_FREE]
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
    ap.add_argument("--run", type=int, metavar="N", help="run N items back-to-back")
    ap.add_argument(
        "--sleep", type=float, default=0.0, help="seconds between --run items"
    )
    ap.add_argument("--id", help="run a specific item id")
    ap.add_argument(
        "--split",
        default="train",
        choices=["all", "train", "eval"],
        help="default 'train' keeps the held-out 'eval' split frozen (never run it).",
    )
    ap.add_argument("--gen", type=int, metavar="N", help="append N verified variants")
    ap.add_argument(
        "--mine", type=int, metavar="N", help="mine N DIVERSE verified items"
    )
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--allow-approval",
        action="store_true",
        help="include approval-requiring tools (sandbox_repl/…) by answering "
        "the CLI prompts (opt-in; bypasses the ALWAYS_CONFIRM tier for this run)",
    )
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--diversity", action="store_true")
    ap.add_argument(
        "--gen-holdout",
        type=int,
        metavar="N",
        default=0,
        help="append N frozen eval-holdout items (never trained on)",
    )
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    if args.allow_approval:
        # Scoped, audited, expiring grant so a headless rollout can use
        # sandbox_repl (ALWAYS_CONFIRM) without a prompt. Revoked at the end.
        try:
            print("offline rollout: " + _grant_offline())
        except Exception as exc:  # noqa: BLE001
            print(f"offline grant failed: {exc}")

    if args.gen:
        n = generate(args.gen, args.seed)
        print(f"generated {n} verified variant(s) -> {GENERATED}")
        return 0
    if args.mine:
        n = mine(args.mine, args.seed)
        cov = coverage()
        print(f"mined {n} diverse verified item(s) -> {GENERATED}")
        print(
            f"coverage: {cov['items']} items  by_tool={cov['by_tool']}  "
            f"by_shape={cov['by_shape']}"
        )
        return 0
    if getattr(args, "gen_holdout", 0):
        m = make_holdout(args.gen_holdout, args.seed)
        print(f"holdout: +{m} items -> {HOLDOUT}")
        return 0
    if args.diversity:
        print(json.dumps(coverage(), indent=2))
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

    if args.run:
        import time as _time

        consec_fail = 0
        for i in range(args.run):
            item = next_item(args.split, approval_free=not args.allow_approval)
            if item is None:
                print("curriculum exhausted")
                break
            print(f"[{i + 1}/{args.run}] [{item['id']}] {item['prompt'][:80]}")
            res = run_item(
                item, timeout=args.timeout, allow_approval=args.allow_approval
            )
            record(res)
            mark = {True: "PASS", False: "FAIL", None: "MANUAL"}[res["verified"]]
            print(
                f"  verified={mark}  tool_hit={res['tool_hit']}  "
                f"tools_used={res['tools_used']}"
            )
            # Back off when the backend looks unhealthy (a hung/down backend
            # otherwise burns the full per-item timeout for hours overnight).
            timed_out = (not res["cli_ok"]) and res["elapsed_s"] >= (args.timeout - 5)
            consec_fail = consec_fail + 1 if timed_out else 0
            if consec_fail >= 3:
                print(
                    f"  [warn] {consec_fail} failed runs — backend may be down; "
                    "sleeping 120s"
                )
                _time.sleep(120)
                consec_fail = 0
            if args.sleep:
                _time.sleep(args.sleep)
        if args.allow_approval:
            try:
                _revoke_offline()
            except Exception:  # noqa: BLE001
                pass
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
    result = run_item(item, timeout=args.timeout, allow_approval=args.allow_approval)
    record(result)
    mark = {True: "PASS", False: "FAIL", None: "MANUAL"}[result["verified"]]
    print(
        f"  cli_ok={result['cli_ok']}  verified={mark}  "
        f"tool_hit={result['tool_hit']}  tools_used={result['tools_used']}"
    )
    print(f"  content: {result['content'][:200]}")
    if args.allow_approval:
        try:
            _revoke_offline()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
