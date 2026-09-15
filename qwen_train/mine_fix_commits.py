"""FIX:/HEAL:-commit task harvester — real repo bugs with a real fail->pass verifier.

Escalation path for when the synthetic fix pool hits the capability ceiling: harvest the
repo's own FIX:/HEAL: commits (the same source `qwen_train/mine_v6.py` uses for training
traces) and turn each into a SWE-bench-style task:

    at PARENT commit  -> a test FAILS
    at FIX    commit  -> that test PASSES      (FAIL_TO_PASS = the verifier)

ISOLATION (hard requirement): each commit is materialized with `git archive <sha>`
extracted into a throwaway temp dir — a SEPARATE copy, never a worktree and never the
live working tree — so the discovery loop can never leave the real checkout detached at
a historical commit.

This module only MINES (emits task descriptors). Running the tasks is the job of a
curriculum harness (the fail->pass tests ARE the verifier, à la DangerRoom.run_tests).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
OUT = _HERE / "curriculum" / "repo_fix_tasks.jsonl"
# Append-only, checked-in contamination denylist (see the file header). The exact commit
# hash is authoritative; date/author/message heuristics are NOT.
DENYLIST = _HERE / "curriculum" / "contaminated_commits.txt"
_CONTAMINATED_REASON = "excluded_contaminated_commit"


def load_denylist(path: Path = DENYLIST) -> dict[str, str]:
    """Parse the append-only denylist -> {full_hash_lower: context/reason}.

    Comments (#) and blank lines are ignored; a malformed line is skipped, never fatal.
    """
    out: dict[str, str] = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        if not parts:
            continue
        out[parts[0].strip().lower()] = parts[2] if len(parts) > 2 else _CONTAMINATED_REASON
    return out


def _git(args: list[str], repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def fix_commits(
    repo: Path = ROOT,
    limit: int | None = None,
    min_age_hours: float = 24.0,
    denylist: dict | None = None,
) -> list[dict]:
    """FIX:/HEAL: commits with >=1 non-test .py file (mirrors mine_v6's harvest).

    `min_age_hours` excludes RECENT commits (default 24 h) — a SECONDARY diagnostic.

    `denylist`: {full_hash_lower: reason}. AUTHORITATIVE contamination filter — a denylisted
    commit is rejected BEFORE it can become a candidate and its rejection is logged
    (`excluded_contaminated_commit`). `None` loads the checked-in denylist; pass `{}` to
    disable (used only for counting the pre-filter total).
    """
    import datetime

    if denylist is None:
        denylist = load_denylist()
    now = datetime.datetime.now(datetime.timezone.utc)
    out = _git(["log", "--pretty=format:%H|%s|%cI"], repo).stdout
    res: list[dict] = []
    skipped_recent = 0
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) < 2:
            continue
        sha, subj = parts[0], parts[1]
        date = parts[2] if len(parts) > 2 else ""
        if not (subj.startswith("FIX:") or subj.startswith("HEAL:")):
            continue
        if sha.lower() in denylist:
            # Rejected from the experiment ≠ erased: record the audit trail.
            print(
                f"[harvest] {_CONTAMINATED_REASON} {sha[:12]} "
                f"({denylist[sha.lower()][:80]})"
            )
            continue
        if min_age_hours:
            try:
                dt = datetime.datetime.fromisoformat(date.replace("Z", "+00:00"))
                if (now - dt).total_seconds() < min_age_hours * 3600:
                    skipped_recent += 1
                    continue
            except ValueError:
                pass
        files = [
            f
            for f in _git(
                ["diff-tree", "--no-commit-id", "--name-only", "-r", sha], repo
            ).stdout.splitlines()
            if f.endswith(".py") and not f.startswith("tests/")
        ]
        if files:
            res.append({"sha": sha, "subject": subj, "files": files})
            if limit and len(res) >= limit:
                break
    if skipped_recent:
        logger_note = f"(skipped {skipped_recent} recent commit(s) < {min_age_hours}h)"
        print(logger_note)
    return res


def extract(sha: str, dest: Path, repo: Path = ROOT) -> bool:
    """Materialize a commit into `dest` via `git archive` (isolated; no worktree)."""
    p = subprocess.run(
        ["git", "-C", str(repo), "archive", sha], capture_output=True
    )  # BINARY stdout — feed the tar bytes straight to tarfile
    if p.returncode != 0:
        return False
    try:
        with tarfile.open(fileobj=BytesIO(p.stdout)) as t:
            t.extractall(dest)
    except Exception:
        return False
    return True


def related_tests(root: Path, changed_file: str, cap: int = 5) -> list[str]:
    """Test files that exercise the changed module, by name then content (mirrors
    agent_service_v2._find_related_tests). Returns paths relative to `root`."""
    tests_dir = root / "tests"
    if not tests_dir.exists():
        return []
    base = Path(changed_file).stem
    mod = str(changed_file).replace("\\", "/").replace(".py", "")
    out: list[Path] = []
    for t in sorted(tests_dir.glob("test_*.py")):
        if base in t.name or t.name.replace("test_", "").replace(".py", "") in base:
            out.append(t)
            if len(out) >= cap:
                break
    if not out:
        for t in sorted(tests_dir.glob("test_*.py")):
            try:
                head = t.read_text(encoding="utf-8", errors="ignore")[:4000]
            except Exception:
                continue
            if mod in head or base in head:
                out.append(t)
                if len(out) >= cap:
                    break
    return [str(t.relative_to(root)).replace("\\", "/") for t in out]


def run_failed(root: Path, test_paths: list[str], timeout: int, python_exe: str) -> set[str]:
    """Run the given tests and return the set of FAILED/ERROR test ids (classname::name)."""
    if not test_paths:
        return set()
    xml = root / "_junit.xml"
    cmd = [
        python_exe,
        "-m",
        "pytest",
        "-q",
        "--tb=no",
        "-p",
        "no:cacheprovider",
        f"--junitxml={xml}",
        "--",
        *test_paths,
    ]
    try:
        subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}
    failed: set[str] = set()
    if xml.exists():
        try:
            for tc in ET.parse(xml).iter("testcase"):
                if tc.find("failure") is not None or tc.find("error") is not None:
                    failed.add(f"{tc.get('classname')}::{tc.get('name')}")
        except ET.ParseError:
            pass
    return failed


def find_flip(
    parent_sha: str,
    fix_sha: str,
    test_paths: list[str],
    repo: Path = ROOT,
    python_exe: str | None = None,
    timeout: int = 180,
) -> dict:
    """FAIL_TO_PASS = tests failing at PARENT that do NOT fail at FIX."""
    python_exe = python_exe or sys.executable
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        if not extract(parent_sha, Path(a), repo) or not extract(fix_sha, Path(b), repo):
            return {"fail_to_pass": [], "error": "extract failed"}
        parent_failed = run_failed(Path(a), test_paths, timeout, python_exe)
        fix_failed = run_failed(Path(b), test_paths, timeout, python_exe)
    f2p = sorted(parent_failed - fix_failed)
    return {
        "fail_to_pass": f2p,
        "parent_failed": sorted(parent_failed),
        "fix_failed": sorted(fix_failed),
        "flip": bool(f2p),
    }


def _mine_one(
    cand: dict, cap: int, timeout: int, python_exe: str, repo: Path
) -> dict | None:
    """One commit -> a task, or None. Extracts PARENT and FIX exactly ONCE each."""
    parent = cand["sha"] + "~1"
    with tempfile.TemporaryDirectory() as dp, tempfile.TemporaryDirectory() as df:
        if not (
            extract(parent, Path(dp), repo) and extract(cand["sha"], Path(df), repo)
        ):
            return None
        # Discover related tests from the FIXED tree (superset of the parent's tests).
        tests: list[str] = []
        for f in cand["files"][:cap]:
            tests += related_tests(Path(df), f)
        tests = list(dict.fromkeys(tests))
        if not tests:
            return None
        parent_failed = run_failed(Path(dp), tests, timeout, python_exe)
        fix_failed = run_failed(Path(df), tests, timeout, python_exe)
        f2p = sorted(parent_failed - fix_failed)  # FAIL_TO_PASS
        if not f2p:
            return None
        return {
            "id": f"repo{cand['sha'][:10]}",
            "sha": cand["sha"],
            "parent": parent,
            "files": cand["files"],
            "tests": tests,
            "fail_to_pass": f2p,
            "prompt": (
                f"The repository is at commit {parent[:10]}. A bug makes these "
                f"test(s) fail: {', '.join(f2p)}. Find and fix it so "
                f"they pass, without changing the tests."
            ),
            "verify": {"type": "fail_to_pass", "tests": f2p},
        }


def mine(
    n: int = 200,
    cap: int = 2,
    timeout: int = 180,
    repo: Path = ROOT,
    min_age_hours: float = 24.0,
    denylist: dict | None = None,
    concurrency: int = 1,
) -> list[dict]:
    """Harvest up to `n` real fail->pass tasks. `concurrency>1` runs K commits in
    parallel (each is 2 `git archive` + 2 pytest runs — I/O+CPU bound, so K helps)."""
    import asyncio

    cands = fix_commits(repo, min_age_hours=min_age_hours, denylist=denylist)
    tasks: list[dict] = []
    if concurrency <= 1:
        for c in cands:
            if len(tasks) >= n:
                break
            t = _mine_one(c, cap, timeout, sys.executable, repo)
            if t:
                tasks.append(t)
        return tasks

    async def _batch(chunk: list[dict]):
        return await asyncio.gather(
            *(
                asyncio.to_thread(_mine_one, c, cap, timeout, sys.executable, repo)
                for c in chunk
            )
        )

    i = 0
    while i < len(cands) and len(tasks) < n:
        chunk = cands[i : i + concurrency]
        i += concurrency
        for r in asyncio.run(_batch(chunk)):
            if r and len(tasks) < n:
                tasks.append(r)
    return tasks


def main() -> int:
    ap = argparse.ArgumentParser(description="Harvest FIX:/HEAL: commits as fail->pass tasks")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument(
        "--concurrency", type=int, default=1, help="K commits harvested in parallel"
    )
    ap.add_argument(
        "--counts",
        action="store_true",
        help="also print before/usable contamination counts (slow: 2 full scans)",
    )
    ap.add_argument(
        "--min-age-hours",
        type=float,
        default=24.0,
        help="skip FIX:/HEAL: commits newer than this (secondary heuristic; the "
        "hash denylist is the authoritative contamination filter)",
    )
    args = ap.parse_args()
    denylist = load_denylist()
    if args.counts:
        before = len(fix_commits(min_age_hours=args.min_age_hours, denylist={}))
        usable = len(fix_commits(min_age_hours=args.min_age_hours))
        print(f"candidates before contamination filtering: {before}")
        print(f"excluded as contaminated: {before - usable}")
        print(f"usable candidates after filtering: {usable}")
    tasks = mine(
        args.n,
        timeout=args.timeout,
        min_age_hours=args.min_age_hours,
        concurrency=args.concurrency,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        for t in tasks:
            json.dump(t, fh, ensure_ascii=False)
            fh.write("\n")
    leaked = [t["sha"] for t in tasks if t["sha"].lower() in denylist]
    print(f"denylisted hashes in usable candidate output: {len(leaked)}")
    print(f"mined {len(tasks)} repo-fix tasks (real fail->pass) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
