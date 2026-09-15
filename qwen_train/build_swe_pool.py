"""SWE-rebench-V2 local pool builder — Docker-free, multi-instance, atomic.

Validates many SWE-rebench-V2 instances in parallel and writes a verified task
pool.  Re-uses all logic from swe_rebench_probe.py — no duplication.

Output (atomic writes):
  qwen_train/curriculum/swe_pool.jsonl       — one record per USABLE instance
  qwen_train/results/swe_pool_summary.json   — probe statistics + by_reason/by_repo

Holdout split: whole repos are held out (never split inside a repo).  The eval
repo list defaults to the 2–3 largest repos by task count; pass --eval-repos to
override.

Usage:
  python qwen_train/build_swe_pool.py --limit 30 --concurrency 2
  python qwen_train/build_swe_pool.py --limit 60 --pages 4 --concurrency 2 \\
      --eval-repos pallets/click pypa/twine
  python qwen_train/build_swe_pool.py --dry-run --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Re-use every proven function from the single-instance probe.  Do NOT copy them.
from swe_rebench_probe import (  # noqa: E402
    WORK,
    ROOT,
    _ensure_interpreter,
    _f2p_result,
    _interpreter_for,
    _list_rows,
    _parse_list_field,
    _pip_cmd,
    _run,
    _test_cmd,
    _venv_minor,
    _minor,
    atomic_write_text,
)
from _atomic import atomic_write_json, atomic_write_jsonl  # noqa: E402

import shutil  # noqa: E402  (after sys.path is set)

_POOL_OUT = _HERE / "curriculum" / "swe_pool.jsonl"
_SUMMARY_OUT = _HERE / "results" / "swe_pool_summary.json"


# ---------------------------------------------------------------------------
# Reason codes (deterministic — never invented)
# ---------------------------------------------------------------------------

REASON_INTERPRETER_MISSING = "interpreter_missing"
REASON_INSTALL_FAILED = "install_failed"
REASON_PATCH_FAILED = "patch_failed"
REASON_F2P_DID_NOT_FAIL = "f2p_did_not_fail_at_base"
REASON_ENV_ERROR = "env_error"
REASON_GOLD_DID_NOT_PASS = "gold_did_not_pass"
REASON_ERROR = "error"
REASON_NOT_PYTHON = "not_python"


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def select_candidates(
    rows: list[dict],
    *,
    language: str = "python",
    prefer_small: bool = True,
) -> list[dict]:
    """Filter rows to the target language, optionally sort by patch size."""
    py = [r for r in rows if r.get("language") == language]
    if prefer_small:
        py.sort(key=lambda r: (r.get("meta") or {}).get("num_modified_lines", 9999))
    return py


def classify_skip_reason(row: dict) -> str | None:
    """Return a skip reason if the candidate can be rejected before probing,
    or None if it should be attempted.  Pure; no I/O."""
    cfg = row.get("install_config") or {}
    base_image = str(cfg.get("base_image_name") or "")
    launcher = _interpreter_for(base_image)
    if launcher is None:
        return REASON_INTERPRETER_MISSING
    return None


# ---------------------------------------------------------------------------
# Per-instance probe (returns a structured dict, never exits)
# ---------------------------------------------------------------------------

def probe_capture(inst: dict) -> dict:
    """Validate one instance end-to-end.  Returns a result dict with keys:
      instance_id, repo, verdict, reason, usable, f2p_base, f2p_gold,
      validated_ts, and (on success) task fields for the pool record.

    All six hard constraints from the task spec are enforced here by delegating
    to the proven helpers in swe_rebench_probe.py.
    """
    instance_id = inst.get("instance_id", "unknown")
    repo = inst.get("repo", "")
    ts = datetime.now(timezone.utc).isoformat()

    def _fail(reason: str, detail: str = "") -> dict:
        print(f"  [{instance_id}] SKIP reason={reason} {detail}")
        return {
            "instance_id": instance_id,
            "repo": repo,
            "verdict": "REJECTED",
            "reason": reason,
            "detail": detail,
            "usable": False,
            "f2p_base": None,
            "f2p_gold": None,
            "validated_ts": ts,
        }

    # ── parse instance fields ──────────────────────────────────────────────
    cfg = inst.get("install_config") or {}
    base_image = str(cfg.get("base_image_name") or "")
    test_cmd_str = str(cfg.get("test_cmd") or "")
    install_steps = cfg.get("install") or []
    if isinstance(install_steps, str):
        install_steps = [install_steps]
    # Constraint 6: parse stringified list correctly
    f2p = _parse_list_field(inst.get("FAIL_TO_PASS"))
    p2p = _parse_list_field(inst.get("PASS_TO_PASS"))
    meta = inst.get("meta") or {}

    # ── interpreter mapping (constraint 3) ────────────────────────────────
    py_launcher = _interpreter_for(base_image)
    if py_launcher is None:
        return _fail(REASON_INTERPRETER_MISSING, f"base_image_name={base_image!r}")

    py_version = _ensure_interpreter(py_launcher, base_image)
    if py_version is None:
        return _fail(REASON_INTERPRETER_MISSING, f"{' '.join(py_launcher)} not installed")

    print(f"  [{instance_id}] interpreter={py_version} image={base_image}")

    # ── workdir (constraint 1: outside repo tree) ─────────────────────────
    d = WORK / instance_id
    src = d / "repo"
    venv = d / "venv"
    d.mkdir(parents=True, exist_ok=True)

    try:
        # ── 1. clone @ base_commit ──────────────────────────────────────────
        base = inst.get("base_commit", "HEAD")
        if not (src / ".git").exists():
            print(f"  [{instance_id}] cloning {repo}…")
            rc, out = _run(
                ["git", "clone", "--quiet", f"https://github.com/{repo}.git", str(src)],
                ROOT,
            )
            if rc != 0:
                return _fail(REASON_ERROR, f"clone rc={rc}: {out[-200:]}")

        rc, out = _run(["git", "checkout", "--quiet", base], src)
        if rc != 0:
            return _fail(REASON_ERROR, f"checkout rc={rc}: {out[-200:]}")

        # ── 2. venv + install (constraints 2, 4) ───────────────────────────
        py = venv / "Scripts" / "python.exe"
        if py.exists():
            got, want = _venv_minor(py), _minor(py_version)
            if got != want:
                print(f"  [{instance_id}] rebuilding venv: {got} != {want}")
                shutil.rmtree(venv, ignore_errors=True)

        if not py.exists():
            rc, out = _run([*py_launcher, "-m", "venv", str(venv)], d, timeout=300)
            if rc != 0:
                return _fail(REASON_INSTALL_FAILED, f"venv rc={rc}: {out[-200:]}")

        _run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], d, timeout=600)

        install_failures: list[str] = []
        for step in install_steps or ["pip install -q -e ."]:
            argv = _pip_cmd(py, str(step))
            rc, out = (
                _run(argv, src, timeout=1500)
                if argv is not None
                else _run(["cmd", "/c", str(step)], src, timeout=1500)
            )
            if rc != 0:
                install_failures.append(f"{step} rc={rc}: {out[-120:]}")

        _run([str(py), "-m", "pip", "install", "-q", "pytest"], src, timeout=600)

        if install_failures:
            print(f"  [{instance_id}] install step failures: {install_failures}")
            # Not immediately fatal — the test run is the real arbiter.

        # ── 3. apply test_patch (constraint 5: reset before apply) ─────────
        tp = d / "test_patch.diff"
        atomic_write_text(tp, inst.get("test_patch") or "")
        _run(["git", "reset", "--hard", "HEAD"], src)
        _run(["git", "clean", "-fd"], src)
        rc, out = _run(
            ["git", "apply", "-v", "--3way", "--recount", "--ignore-space-change", str(tp)],
            src,
        )
        if rc != 0:
            rc, out = _run(["git", "apply", "-v", str(tp)], src)
        if rc != 0:
            return _fail(REASON_PATCH_FAILED, f"test_patch apply rc={rc}: {out[-200:]}")

        # ── 4. run at base → F2P must FAIL ─────────────────────────────────
        print(f"  [{instance_id}] running tests at base…")
        _rc, base_out = _run(_test_cmd(py, test_cmd_str), src, timeout=900)
        (d / "run_at_base.txt").write_text(base_out, encoding="utf-8")
        base_passed, base_failed = _f2p_result(base_out, f2p)
        at_base_ok = base_failed > 0 and base_passed == 0
        print(
            f"  [{instance_id}] base: f2p passed={base_passed} failed={base_failed}"
            f" → {'OK' if at_base_ok else 'UNEXPECTED'}"
        )
        if base_passed == 0 and base_failed == 0:
            return _fail(
                REASON_ENV_ERROR,
                "f2p passed=0 failed=0 at base (likely broken env)",
            )
        if not at_base_ok:
            return _fail(
                REASON_F2P_DID_NOT_FAIL,
                f"f2p passed={base_passed} failed={base_failed} at base",
            )

        # ── 5. apply gold patch → F2P must PASS ────────────────────────────
        print(f"  [{instance_id}] applying gold patch…")
        gp = d / "gold_patch.diff"
        atomic_write_text(gp, inst.get("patch") or "")
        rc2, _out2 = _run(
            ["git", "apply", "-v", "--3way", "--recount", "--ignore-space-change", str(gp)],
            src,
        )
        if rc2 != 0:
            rc2, _out2 = _run(["git", "apply", "-v", str(gp)], src)
        if rc2 != 0:
            return _fail(REASON_PATCH_FAILED, f"gold_patch apply rc={rc2}: {_out2[-200:]}")

        _rc3, gold_out = _run(_test_cmd(py, test_cmd_str), src, timeout=900)
        (d / "run_at_gold.txt").write_text(gold_out, encoding="utf-8")
        gold_passed, gold_failed = _f2p_result(gold_out, f2p)
        gold_ok = gold_passed > 0 and gold_failed == 0
        print(
            f"  [{instance_id}] gold: f2p passed={gold_passed} failed={gold_failed}"
            f" → {'OK' if gold_ok else 'UNEXPECTED'}"
        )
        if not gold_ok:
            return _fail(
                REASON_GOLD_DID_NOT_PASS,
                f"f2p passed={gold_passed} failed={gold_failed} at gold",
            )

    except Exception as exc:  # noqa: BLE001
        print(f"  [{instance_id}] unexpected error: {exc}")
        return _fail(REASON_ERROR, str(exc)[:300])

    print(f"  [{instance_id}] USABLE ✓ (base {base_failed} fail → gold {gold_passed} pass)")
    return {
        "instance_id": instance_id,
        "repo": repo,
        "base_commit": inst.get("base_commit"),
        "language": inst.get("language", "python"),
        "image_name": inst.get("image_name", ""),
        "base_image_name": base_image,
        "test_cmd": test_cmd_str,
        "fail_to_pass": f2p,
        "pass_to_pass": p2p,
        "install": install_steps,
        "num_modified_lines": meta.get("num_modified_lines"),
        "num_modified_files": meta.get("num_modified_files"),
        "validated_ts": ts,
        "verdict": "USABLE (docker-free)",
        "reason": None,
        "usable": True,
        "f2p_base": {"passed": base_passed, "failed": base_failed},
        "f2p_gold": {"passed": gold_passed, "failed": gold_failed},
    }


# ---------------------------------------------------------------------------
# Split assignment: hold out whole repos
# ---------------------------------------------------------------------------

def assign_splits(
    usable: list[dict],
    eval_repos: list[str] | None = None,
) -> list[dict]:
    """Tag each record split='train'|'eval'.

    If eval_repos is given, those repos are eval.  Otherwise the 2–3 largest
    repos by task count are held out.  Split is assigned by REPO, never inside
    a repo (constraint 4 of the spec).
    """
    if not usable:
        return usable

    if eval_repos:
        eval_set = set(eval_repos)
    else:
        # Count tasks per repo, hold out the 2–3 largest
        from collections import Counter
        counts = Counter(r["repo"] for r in usable)
        top = [repo for repo, _ in counts.most_common(3)]
        # Only hold out if there are enough repos to have a non-empty train set
        if len(counts) <= len(top):
            top = top[:1]  # keep at least 1 repo for eval, rest for train
        eval_set = set(top)

    for rec in usable:
        rec["split"] = "eval" if rec["repo"] in eval_set else "train"
    return usable


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def build_summary(
    probed: int,
    results: list[dict],
) -> dict:
    """Pure function: build the summary dict from all probe results."""
    usable = [r for r in results if r.get("usable")]
    rejected = [r for r in results if not r.get("usable")]

    by_reason: dict[str, int] = {}
    for r in rejected:
        reason = r.get("reason") or REASON_ERROR
        by_reason[reason] = by_reason.get(reason, 0) + 1

    by_repo: dict[str, int] = {}
    for r in usable:
        repo = r.get("repo", "unknown")
        by_repo[repo] = by_repo.get(repo, 0) + 1

    return {
        "probed": probed,
        "usable": len(usable),
        "rejected": len(rejected),
        "by_reason": by_reason,
        "by_repo": by_repo,
        "generated_ts": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Async pool builder
# ---------------------------------------------------------------------------

async def build_pool(
    *,
    limit: int = 30,
    pages: int = 4,
    concurrency: int = 1,
    eval_repos: list[str] | None = None,
    dry_run: bool = False,
    language: str = "python",
) -> dict:
    """Fetch candidates, probe each one, write outputs atomically."""

    # Hard guard: work dir must be outside the repo
    if str(WORK.resolve()).lower().startswith(str(ROOT.resolve()).lower()):
        print(f"ERROR: SWE_PROBE_WORK={WORK} is INSIDE the repo ({ROOT}) — refusing.")
        print("Set SWE_PROBE_WORK to a directory outside the project tree.")
        return {}

    print(f"Fetching rows (pages={pages})…")
    rows = _list_rows(pages)
    candidates = select_candidates(rows, language=language)
    print(f"  {len(rows)} rows fetched → {len(candidates)} {language} candidates")

    # Apply pre-filter (interpreter check) and limit
    pre_filtered: list[dict] = []
    pre_skip: list[dict] = []
    for row in candidates:
        reason = classify_skip_reason(row)
        if reason:
            pre_skip.append({"instance_id": row.get("instance_id"), "reason": reason})
        else:
            pre_filtered.append(row)

    print(
        f"  {len(pre_filtered)} have a mappable interpreter,"
        f" {len(pre_skip)} pre-skipped (interpreter_missing)"
    )

    to_probe = pre_filtered[:limit]
    print(f"  probing {len(to_probe)} (limit={limit}, concurrency={concurrency})")

    if dry_run:
        print("[dry-run] would probe:", [r.get("instance_id") for r in to_probe])
        return {}

    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []
    results_lock = threading.Lock()

    async def _run_one(inst: dict) -> None:
        async with sem:
            result = await asyncio.to_thread(probe_capture, inst)
            with results_lock:
                results.append(result)

    await asyncio.gather(*[_run_one(inst) for inst in to_probe])

    # Add pre-skip entries so they appear in the summary
    for skip in pre_skip[:limit]:  # only skips within the limit window
        results.append({
            "instance_id": skip["instance_id"],
            "repo": "",
            "verdict": "REJECTED",
            "reason": skip["reason"],
            "usable": False,
            "f2p_base": None,
            "f2p_gold": None,
            "validated_ts": datetime.now(timezone.utc).isoformat(),
        })

    usable = assign_splits(
        [r for r in results if r.get("usable")],
        eval_repos=eval_repos,
    )

    # ── atomic writes ──────────────────────────────────────────────────────
    _POOL_OUT.parent.mkdir(parents=True, exist_ok=True)
    _SUMMARY_OUT.parent.mkdir(parents=True, exist_ok=True)

    # Pool JSONL — USABLE records only (atomic_write_jsonl takes dicts)
    atomic_write_jsonl(_POOL_OUT, usable)
    print(f"Wrote {len(usable)} USABLE records → {_POOL_OUT}")

    summary = build_summary(len(to_probe) + len(pre_skip[:limit]), results)
    atomic_write_json(_SUMMARY_OUT, summary)
    print(f"Wrote summary → {_SUMMARY_OUT}")
    print(json.dumps(summary, indent=2))

    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build a local Docker-free SWE-rebench-V2 task pool"
    )
    ap.add_argument("--limit", type=int, default=30, help="Max candidates to probe")
    ap.add_argument("--pages", type=int, default=4, help="Dataset pages to fetch (60 rows each)")
    ap.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Parallel probes (each clones a repo + builds a venv; K=2 is safe)",
    )
    ap.add_argument(
        "--eval-repos",
        nargs="*",
        metavar="REPO",
        help="Repos to hold out for eval (e.g. pallets/click).  Defaults to 2–3 largest.",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print candidates without running")
    ap.add_argument("--language", default="python", help="Language filter (default: python)")
    args = ap.parse_args()

    summary = asyncio.run(
        build_pool(
            limit=args.limit,
            pages=args.pages,
            concurrency=args.concurrency,
            eval_repos=args.eval_repos,
            dry_run=args.dry_run,
            language=args.language,
        )
    )
    return 0 if summary else 1


if __name__ == "__main__":
    sys.exit(main())
