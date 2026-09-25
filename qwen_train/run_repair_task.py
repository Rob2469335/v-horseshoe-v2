"""Run ONE local repair task through the agent harness, using a LOCAL hf_inst.

Thin adapter (NOT a harness change): reuses `cli_baseline_swe`'s
`_reset_instance` / `_build_prompt` / `_run_tests` / `_test_result` and
`run_curriculum._attempt_once`, but supplies `hf_inst` (problem_statement +
test_patch) from a task record instead of fetching from HuggingFace.

F1 HARNESS ARCHITECTURE (2026-09-25):
  - Evaluator lives OUTSIDE the target workspace
  - Agent may modify ONLY the target workspace
  - Prompt describes behavioral defect, NOT implementation fix
  - Pre-flight checks verify all boundaries before observation
  - Dry-run mode validates without invoking the model

Usage:
    python qwen_train/run_repair_task.py \\
        --instance-id f1_pilot/run_1_fresh \\
        --base-commit 45d9f6192dd7b1c81c63f46d277798f19adb97ec \\
        --problem-statement "..." \\
        --test-cmd "python -m pytest C:\\...\\evaluator\\test_sandbox_bounds.py -v" \\
        --f2p test_write_root_subdir_of_workspace \\
        --f2p test_workspace_inside_write_root \\
        --f2p test_no_write_root \\
        --evaluator-dir "C:\\...\\evaluator"

The repo is expected at probe.WORK/<instance_id>/repo already checked out /
committed at --base-commit (the BROKEN state). The adapter resets to that commit
so the run starts from exactly the buggy state.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import run_curriculum as rc  # noqa: E402
import cli_baseline_swe as cls  # noqa: E402
from swe_rebench_probe import WORK  # noqa: E402

# F1 authorized base commit (from docs/EXPERIMENT_J_F1_AUTHORIZATION.md)
F1_AUTHORIZED_BASE_COMMIT = "45d9f6192dd7b1c81c63f46d277798f19adb97ec"


def _preflight_evaluator_separation(repo: Path, evaluator_dir: Path) -> list[str]:
    """Verify evaluator is outside target workspace. Returns list of errors (empty = OK)."""
    errors = []
    repo_resolved = repo.resolve()
    eval_resolved = evaluator_dir.resolve()

    # Check evaluator exists
    if not evaluator_dir.exists():
        errors.append(f"Evaluator directory does not exist: {evaluator_dir}")
        return errors

    # Check evaluator is NOT inside target workspace
    try:
        eval_resolved.relative_to(repo_resolved)
        errors.append(
            f"EVALUATOR CONTAMINATION: evaluator ({eval_resolved}) is INSIDE "
            f"target workspace ({repo_resolved}). This violates layer separation."
        )
    except ValueError:
        pass  # Good - evaluator is outside target

    # Check target workspace is NOT inside evaluator
    try:
        repo_resolved.relative_to(eval_resolved)
        errors.append(
            f"TARGET INSIDE EVALUATOR: target workspace ({repo_resolved}) is INSIDE "
            f"evaluator ({eval_resolved}). This violates layer separation."
        )
    except ValueError:
        pass  # Good - target is outside evaluator

    return errors


def _preflight_target_state(repo: Path, expected_commit: str) -> list[str]:
    """Verify target workspace is at the authorized base commit and clean."""
    errors = []

    # Check .git exists
    if not (repo / ".git").exists():
        errors.append(f"Target workspace has no .git directory: {repo}")
        return errors

    # Check HEAD matches authorized commit
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), capture_output=True, text=True
    )
    actual_head = result.stdout.strip()
    if actual_head != expected_commit:
        errors.append(
            f"Target HEAD mismatch: expected {expected_commit}, got {actual_head}"
        )

    # Check working tree is clean
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo), capture_output=True, text=True
    )
    if result.stdout.strip():
        errors.append(
            f"Target working tree is not clean:\n{result.stdout.strip()}"
        )

    return errors


def _preflight_f1_controls() -> list[str]:
    """Verify F1 control environment is correct."""
    errors = []

    # Check required controls
    controls = {
        "SWARM_MEMORY_INJECT": "0",
        "SWARM_AUTONOMY": "0",
        "SWARM_NO_TOASTS": "1",
        "SWARM_SEMANTIC_CACHE": "0",
        "SWARM_GENETIC_MUTATION": "0",
    }
    for key, expected in controls.items():
        actual = os.environ.get(key)
        if actual != expected:
            errors.append(f"F1 control {key}={actual!r}, expected {expected!r}")

    # Check SWARM_WORKSPACE_ROOT is set and points to target
    workspace_root = os.environ.get("SWARM_WORKSPACE_ROOT")
    if not workspace_root:
        errors.append("SWARM_WORKSPACE_ROOT is not set")
    elif not Path(workspace_root).resolve().exists():
        errors.append(f"SWARM_WORKSPACE_ROOT points to nonexistent path: {workspace_root}")

    return errors


def _build_behavioral_prompt(inst: dict, hf_inst: dict) -> str:
    """Build a behavioral task prompt that does NOT disclose the implementation fix.

    The prompt describes:
    - What behavior is incorrect
    - What behavior is expected
    - Relevant function/module names
    - How to verify behavior

    The prompt does NOT disclose:
    - The exact line to change
    - The exact expression replacement
    - The exact implementation strategy
    - base.relative_to(root) or root.relative_to(base)
    """
    repo = (Path(os.environ.get("SWARM_WORKSPACE_ROOT", "")) or
            (WORK / inst["instance_id"] / "repo")).resolve()
    ps = (hf_inst.get("problem_statement") or "").strip()
    test_cmd = inst.get("test_cmd") or "python -m pytest"

    return (
        f"Work inside this repository -- your filesystem tools can read and write "
        f"under it:\n{repo}\n\n"
        f"Problem:\n{ps}\n\n"
        f"Fix the problem in that repository so the failing tests pass.\n\n"
        f"TOOL CONTRACT -- follow this or you will loop and waste the turn budget:\n"
        f"- You have a SHELL: `sandbox_repl` with language=\"bash\" and a `command`. "
        f"Use it to look around and to verify (e.g. `git diff`, `ls`, "
        f"`python -m pytest -q tests/test_x.py`).\n"
        f"- EDIT files with the `filesystem` tool -- operation=patch with `old` = the "
        f"EXACT existing text and `new` = the replacement (read the file first so "
        f"`old` matches exactly), or operation=write for a whole file.\n"
        f"- Do NOT use `sandbox_repl` language=\"python\" to read files -- its "
        f"Security Gate blocks `open()`/`pathlib`. Use the shell or `filesystem`.\n"
        f"- The tests that must pass are run by:\n  {test_cmd}\n"
        f"- Do NOT modify test files.\n"
        f"- Do NOT create files outside the repository."
    )


def _prompt_contains_exact_fix(prompt: str) -> bool:
    """Check if prompt accidentally reveals the exact implementation fix."""
    # Patterns that would give away the fix
    dangerous_patterns = [
        "base.relative_to(root)",
        "root.relative_to(base)",
        "base.relative_to(",
        "root.relative_to(",
        "relative_to(base)",
        "relative_to(root)",
        "change base.relative_to",
        "change root.relative_to",
        "replace base.relative_to",
        "replace root.relative_to",
        "The fix is to change",
        "The fix is to replace",
        "inverted relative_to",
    ]
    prompt_lower = prompt.lower()
    for pattern in dangerous_patterns:
        if pattern.lower() in prompt_lower:
            return True
    return False


def _run_evaluator_sanity_check(
    evaluator_dir: Path,
    repo: Path,
    py: Path,
    test_cmd: str,
) -> tuple[bool, str]:
    """Run evaluator against buggy code to verify it detects the bug.

    Returns (all_pass, output).
    Expected: Case A FAIL, Case B FAIL, Case C PASS.
    """
    test_env = {k: v for k, v in os.environ.items()
                if k not in ("PYTHONPATH", "PYTHONHOME")}

    # Run the evaluator
    cmd = test_cmd.replace("python -m pytest", f'"{py}" -m pytest')
    p = subprocess.run(
        cmd, shell=True,
        cwd=str(repo), capture_output=True, text=True, timeout=120,
        env=test_env,
    )
    output = (p.stdout or "") + "\n" + (p.stderr or "")

    # Parse results
    has_failures = "FAILED" in output
    has_passes = "PASSED" in output

    # We expect some failures (Case A and B) and some passes (Case C)
    # So "all pass" should be False on buggy code
    return not has_failures, output


def main() -> int:
    ap = argparse.ArgumentParser(
        description="F1 harness for repair tasks with evaluator separation"
    )
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--base-commit", required=True)
    ap.add_argument("--problem-statement", required=True)
    ap.add_argument("--test-cmd", required=True)
    ap.add_argument("--f2p", action="append", default=[])
    ap.add_argument("--test-patch", default="")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--out", default="qwen_train/results/repair_task.jsonl")
    ap.add_argument("--evaluator-dir", default="",
                    help="Directory containing evaluator tests (must be outside target workspace)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run pre-flight checks and evaluator sanity without invoking the model")
    ap.add_argument("--behavioral-prompt", action="store_true",
                    help="Use behavioral prompt (no exact fix disclosure)")
    args = ap.parse_args()

    instance_id = args.instance_id
    repo = (WORK / instance_id / "repo").resolve()
    evaluator_dir = Path(args.evaluator_dir).resolve() if args.evaluator_dir else None

    print("=" * 70)
    print("F1 HARNESS PRE-FLIGHT CHECKS")
    print("=" * 70)

    # --- Pre-flight: Target workspace ---
    print("\n[1/6] Checking target workspace...")
    target_errors = _preflight_target_state(repo, args.base_commit)
    if target_errors:
        for err in target_errors:
            print(f"  FAIL: {err}")
        print("\nABORT: Target workspace pre-flight failed.")
        return 2
    print(f"  PASS: Target at {repo}")
    print(f"  PASS: HEAD = {args.base_commit}")
    print(f"  PASS: Working tree clean")

    # --- Pre-flight: Evaluator separation ---
    print("\n[2/6] Checking evaluator separation...")
    if evaluator_dir:
        eval_errors = _preflight_evaluator_separation(repo, evaluator_dir)
        if eval_errors:
            for err in eval_errors:
                print(f"  FAIL: {err}")
            print("\nABORT: Evaluator separation pre-flight failed.")
            return 2
        print(f"  PASS: Evaluator at {evaluator_dir}")
        print(f"  PASS: Evaluator is OUTSIDE target workspace")
    else:
        print("  WARN: No --evaluator-dir specified; evaluator separation not verified")

    # --- Pre-flight: F1 controls ---
    print("\n[3/6] Checking F1 control environment...")
    control_errors = _preflight_f1_controls()
    if control_errors:
        for err in control_errors:
            print(f"  FAIL: {err}")
        print("\nABORT: F1 control pre-flight failed.")
        return 2
    print("  PASS: All F1 controls correct")

    # --- Pre-flight: Prompt integrity ---
    print("\n[4/6] Checking prompt integrity...")
    inst = {
        "instance_id": instance_id,
        "base_commit": args.base_commit,
        "test_cmd": args.test_cmd,
        "fail_to_pass": args.f2p or [],
        "pass_to_pass": [],
        "split": "repair",
    }
    test_patch_content = ""
    if args.test_patch and Path(args.test_patch).exists():
        test_patch_content = Path(args.test_patch).read_text("utf-8")
    hf_inst = {"problem_statement": args.problem_statement, "test_patch": test_patch_content}

    if args.behavioral_prompt:
        prompt = _build_behavioral_prompt(inst, hf_inst)
    else:
        prompt = cls._build_prompt(inst, hf_inst)

    if _prompt_contains_exact_fix(prompt):
        print("  FAIL: Prompt contains exact implementation fix")
        print("  The prompt must describe BEHAVIOR, not implementation")
        print("\nABORT: Prompt integrity check failed.")
        return 2
    print("  PASS: Prompt does not disclose exact implementation fix")

    # --- Pre-flight: Evaluator sanity check ---
    print("\n[5/6] Running evaluator sanity check (NOT AN F1 OBSERVATION)...")
    if evaluator_dir:
        # Find Python
        py = Path(sys.executable).parent.parent / "Scripts" / "python.exe"
        if not py.exists():
            py = Path(sys.executable)

        # Reset target to base commit for clean evaluation
        cls._reset_instance(inst, hf_inst)

        all_pass, eval_output = _run_evaluator_sanity_check(
            evaluator_dir, repo, py, args.test_cmd
        )
        if all_pass:
            print("  FAIL: All evaluator tests passed (expected some failures on buggy code)")
            print("  This suggests the evaluator is not testing the right behavior")
        else:
            print("  PASS: Evaluator correctly detects bugs in target code")
            print("  (Case A: FAIL, Case B: FAIL, Case C: PASS expected)")
        print("\n  EVALUATOR OUTPUT (abbreviated):")
        for line in eval_output.split("\n")[-20:]:
            if line.strip():
                print(f"    {line}")
    else:
        print("  SKIP: No evaluator directory specified")

    # --- Pre-flight: Target pollution check ---
    print("\n[6/6] Checking target workspace for pollution...")
    contamination_files = [
        "tests/test_repair_task1.py",
        "create_test.py",
        "agent_sdk_toolkit/workspace/bounds.rs",
    ]
    found_contamination = False
    for cf in contamination_files:
        if (repo / cf).exists():
            print(f"  FAIL: Contamination found: {cf}")
            found_contamination = True
    if not found_contamination:
        print("  PASS: No contamination files found in target workspace")

    print("\n" + "=" * 70)
    print("PRE-FLIGHT COMPLETE")
    print("=" * 70)

    # --- Dry run mode ---
    if args.dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN MODE -- NOT AN F1 OBSERVATION")
        print("=" * 70)
        print("\nDry run validated all pre-flight checks.")
        print("No model was invoked. No observation was created.")
        print("\nTarget workspace remains at:", repo)
        print("Target HEAD:", args.base_commit)
        print("Evaluator remains at:", evaluator_dir or "(not specified)")
        return 0

    # --- Full observation mode ---
    print("\n" + "=" * 70)
    print("F1 OBSERVATION MODE")
    print("=" * 70)

    # Reset target to base commit
    cls._reset_instance(inst, hf_inst)
    f2p = cls.probe._parse_list_field(inst.get("fail_to_pass") or [])

    # Find Python
    py = Path(sys.executable).parent.parent / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    test_env = {k: v for k, v in os.environ.items()
                if k not in ("PYTHONPATH", "PYTHONHOME")}

    def _test_once() -> str:
        cmd = args.test_cmd.replace("python -m pytest", f'"{py}" -m pytest')
        p = subprocess.run(
            cmd, shell=True,
            cwd=str(repo), capture_output=True, text=True, timeout=600,
            env=test_env,
        )
        return (p.stdout or "") + (p.stderr or "")

    base_failing = cls._failing_ids(_test_once())
    base_p2p_fail = set()
    print(f"base: {len([t for t in f2p if t in base_failing])}/{len(f2p)} f2p failing")

    item = {"id": instance_id, "prompt": prompt, "split": "repair"}
    os.environ["SWARM_WORKSPACE_ROOT"] = str(repo)
    # Experiment J F1: strip web_search/web_fetch from the coder's tool surface
    # to prevent drift into web research on this local repair task.
    os.environ["SWARM_F1_NO_WEB_TOOLS"] = "1"

    # Grant task-scoped offline tool access
    grant_msg = rc._grant_offline()
    print(f"offline grants: {grant_msg}")
    try:
        from swarm_os.services.trust_ledger import grant
        grant("mcp", 8 * 3600)
        grant("web_fetch", 8 * 3600)
        grant("filesystem", 8 * 3600)
        print("offline grants: mcp, web_fetch, filesystem (8h)")
    except Exception as exc:
        print(f"grant failed: {exc}")

    # --- Immediate health gate (PID identity + endpoint health) ---
    print("\n[IMMEDIATE HEALTH GATE]")
    try:
        import importlib
        f1i = importlib.import_module("f1_infra")
        _backend_pid = f1i._check_port_listening(8000)
        _health_ok = f1i._probe_endpoint("http://127.0.0.1:8000/health", timeout=10.0)
        _status_ok = False
        _workspace_match = False
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/status", timeout=10.0) as _r:
                _status_data = json.loads(_r.read().decode())
                _status_ok = _status_data.get("ready", False)
                _workspace_match = _status_data.get("sandbox", {}).get("workspace_root", "") == str(repo)
        except Exception:
            pass
        print(f"  port_listening: {_backend_pid is not None} (PID={_backend_pid})")
        print(f"  health_http_200: {_health_ok}")
        print(f"  status_ready: {_status_ok}")
        print(f"  workspace_match: {_workspace_match}")
        if not (_backend_pid and _health_ok and _status_ok and _workspace_match):
            print("  HEALTH GATE FAILED — ABORTING OBSERVATION")
            _obs_result = {
                "ts": f1i._now_iso(),
                "id": instance_id,
                "verdict": False,
                "verify_reason": "health_gate_failure",
                "infrastructure_invalid_reason": "health_gate_failure",
                "validity": {"infrastructure": "INVALID"},
                "capability_credit": {"positive": 0, "negative": 0},
                "f1_harness": {"health_gate_passed": False, "pid": _backend_pid},
            }
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            with open(args.out, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_obs_result) + "\n")
            print(json.dumps(_obs_result, indent=2))
            return 2
        print("  HEALTH GATE PASSED")
    except Exception as _gate_exc:
        print(f"  Health gate import/probe failed: {_gate_exc}")
        print("  HEALTH GATE FAILED — ABORTING OBSERVATION")
        return 2

    try:
        res = rc._attempt_once(item, args.timeout, allow_approval=True, record=False)
    finally:
        rc._revoke_offline()

    out = _test_once()
    after_failing = cls._failing_ids(out)
    ok, reason = cls._test_result(out, f2p, [], base_p2p_fail)
    res["verdict"] = ok
    res["verify_reason"] = reason
    res["elapsed_s"] = round(float(res.get("elapsed_s", 0)), 1)

    # Record F1 harness metadata
    res["f1_harness"] = {
        "evaluator_dir": str(evaluator_dir) if evaluator_dir else None,
        "target_workspace": str(repo),
        "base_commit": args.base_commit,
        "behavioral_prompt": args.behavioral_prompt,
        "preflight_passed": True,
        "prompt_integrity": not _prompt_contains_exact_fix(prompt),
    }

    # True source diff
    diff = subprocess.run(
        ["git", "diff", "--stat", args.base_commit, "HEAD"],
        cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()
    res["diff_stat"] = diff

    # Learning bridge
    try:
        import asyncio
        from runtime_v2.api.evaluation_bridge import build_and_submit_evaluation_failure

        f2p_set = set(f2p)
        bridge_result = asyncio.run(
            build_and_submit_evaluation_failure(
                task_id=instance_id,
                rollout_id=str(uuid.uuid4()),
                res=res,
                f2p_p=len(f2p) - len(f2p_set & base_failing),
                f2p_f=len(f2p_set & base_failing),
                f2p_p2=len(f2p) - len(f2p_set & after_failing),
                f2p_f2=len(f2p_set & after_failing),
                diff_stat=diff,
                ok=ok,
                timeout_seconds=args.timeout,
                agent_model="robs4b",
                routing_mode=os.environ.get("SWARM_ROUTING_MODE", "unknown"),
                run_ids=res.get("run_ids"),
            )
        )
        res["bridge_result"] = bridge_result
        print(f"  bridge_result: {bridge_result}")
    except Exception as bridge_err:
        print(f"  bridge_error: {bridge_err}")
        res["bridge_error"] = str(bridge_err)

    res["f2p"] = inst["fail_to_pass"]
    res["after_failing"] = sorted(after_failing)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "a", encoding="utf-8") as f:
        f.write(json.dumps(res) + "\n")
    print(json.dumps(res, indent=2))
    print(f"VERDICT: ok={ok} reason={reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
