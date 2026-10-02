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
from swe_rebench_probe import (  # noqa: E402
    WORK,
    _ensure_interpreter,
    _interpreter_for,
    _pip_cmd,
    _test_cmd,
)


class TaskEnvironmentError(RuntimeError):
    """Raised when a task's declared test environment cannot be established.

    Fails closed on purpose: silently substituting the project interpreter
    (Python 3.14) for a task that declared `python_base_310` produces
    environment failures that masquerade as benchmark behaviour failures. That
    is the documented incident in `swe_rebench_probe._interpreter_for`.
    """


_POOL_ENV_CACHE: dict[str, dict] = {}


def _pool_env_meta(instance_id: str) -> dict:
    """Read task-environment metadata (`base_image_name`, `install`) from the pool.

    The runner previously synthesised its `inst` dict from CLI arguments only, so
    it never saw the curriculum's declared environment. This reads the authoritative
    row from `qwen_train/curriculum/swe_pool.jsonl`. Fails closed by returning an
    empty dict when the row is absent, which makes `resolve_task_python` raise.
    """
    if instance_id in _POOL_ENV_CACHE:
        return _POOL_ENV_CACHE[instance_id]
    meta: dict = {}
    pool = _HERE / "curriculum" / "swe_pool.jsonl"
    try:
        for line in pool.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("instance_id") == instance_id:
                meta = {
                    "base_image_name": row.get("base_image_name") or "",
                    "install": row.get("install") or "",
                    "image_name": row.get("image_name") or "",
                }
                break
    except (OSError, ValueError):
        meta = {}
    _POOL_ENV_CACHE[instance_id] = meta
    return meta


def resolve_task_python(inst: dict) -> list[str]:
    """Return the interpreter launcher for a curriculum row's `base_image_name`.

    Reuses `swe_rebench_probe._interpreter_for` / `_ensure_interpreter` so there is
    exactly one source of truth for the Python-version mapping and the
    fail-closed behaviour. Raises `TaskEnvironmentError` rather than falling back
    to `sys.executable`.
    """
    base_image = str(inst.get("base_image_name") or "")
    launcher = _interpreter_for(base_image)
    if not launcher:
        raise TaskEnvironmentError(
            f"base_image_name={base_image!r} is not mappable to a local interpreter; "
            "refusing to fall back to the project interpreter"
        )
    version = _ensure_interpreter(launcher, base_image)
    if not version:
        raise TaskEnvironmentError(
            f"base_image_name={base_image!r} -> {' '.join(launcher)} is not available; "
            "install that interpreter or skip this instance"
        )
    return launcher


def task_test_argv(task_py: Path | str, test_cmd: str) -> list[str]:
    """Build the task-test argv for a curriculum row's `test_cmd`.

    Delegates to `swe_rebench_probe._test_cmd`, which rewrites a bare
    `pytest ...` into `<task python> -m pytest ...`. The previous inline
    `test_cmd.replace("python -m pytest", ...)` in this module was inert because
    every pool command begins with bare `pytest`, so the executable resolved
    through ambient PATH instead of the task environment.

    `_test_cmd` drops trailing arguments for the `python -m pytest ...` spelling
    (it only splits the bare `pytest` form), so that form is normalised here
    before delegating. `swe_rebench_probe._test_cmd` itself is left untouched so
    the pool builder keeps its existing behaviour.
    """
    raw = test_cmd.strip()
    if raw.startswith("python -m pytest"):
        return _test_cmd(Path(task_py), "pytest" + raw[len("python -m pytest"):])
    head = raw.split()[0] if raw.split() else ""
    if head in ("pytest", "py.test", "-m"):
        return _test_cmd(Path(task_py), raw)
    # A non-pytest task command is preserved verbatim as argv. It is NOT run
    # through the project interpreter and NOT executed with shell=True; the task
    # environment is still selected by running it under the task venv's python
    # when the command is a script, otherwise it is returned unchanged so the
    # caller's own execution policy applies.
    import shlex

    return shlex.split(raw)


def _task_python_path(launcher: list[str]) -> Path:
    """Resolve an interpreter launcher to a concrete executable path.

    `_interpreter_for` returns a launcher such as ``["py", "-3.10"]``. For argv
    construction we need the interpreter that launcher resolves to, so the minor
    version is queried once and its real executable path returned.
    """
    if len(launcher) == 1:
        return Path(launcher[0])
    # Ask the launcher which interpreter it actually resolves to. Returning the
    # launcher name alone would silently fall back to the default interpreter
    # (Python 3.14 on this host), which is exactly the defect being fixed.
    probe = subprocess.run(
        [*launcher, "-c", "import sys;print(sys.executable)"],
        capture_output=True, text=True, timeout=120,
    )
    resolved = (probe.stdout or "").strip().splitlines()
    if probe.returncode == 0 and resolved and Path(resolved[-1]).exists():
        return Path(resolved[-1])
    spec = launcher[1] if launcher[0].lower() in ("py", "python") else launcher[0]
    major, _, minor = spec.lstrip("-").partition(".")
    root = Path(sys.executable).parent
    for cand in (root / f"python{major}.{minor}.exe", root / f"python{major}{minor}.exe"):
        if cand.exists():
            return cand
    return Path(launcher[0])


def task_install_argv(task_py: Path | str, step: str) -> list[str] | None:
    """Build the argv for one curriculum `install` step against the task venv.

    Returns None for a non-pip step; the caller shells those. Delegates to
    `swe_rebench_probe._pip_cmd` so pip can never target the project `.venv`.
    """
    return _pip_cmd(Path(task_py), step)


# Execution prerequisite owned by the HARNESS, not by curriculum truth.
#
# Most curriculum rows supply a test runner through their own `install` field
# (Werkzeug via requirements/tests.txt, dbt via dbt-tests-adapter, pandas-ai by
# naming pytest explicitly). `qiskit__qiskit-ibm-runtime-367` declares only
# `pip install -e .[test,common] --quiet`, which does not install pytest, yet its
# `test_cmd` begins with `pytest`. Rather than silently editing curriculum truth,
# the harness bootstraps the runner it needs and records that it did so.
#
# Provenance matters for Experiment J: a package the harness supplied must not
# be attributed to the curriculum row.
HARNESS_EXECUTION_PREREQS = ("pytest",)

CURRICULUM = "curriculum"
HARNESS = "harness"


def task_pytest_available(task_py: Path | str) -> bool:
    """True when the task venv can already import the declared test runner."""
    import importlib.util

    for mod in HARNESS_EXECUTION_PREREQS:
        probe = subprocess.run(
            [str(task_py), "-c",
             f"import importlib.util as u;print('Y' if u.find_spec('{mod}') else 'N')"],
            capture_output=True, text=True, timeout=120,
        )
        if probe.returncode != 0 or "Y" not in (probe.stdout or ""):
            return False
    return True


def ensure_task_pytest(task_py: Path | str) -> list[dict]:
    """Bootstrap the test runner into the task venv if curriculum did not supply it.

    Returns provenance records: one entry per package, stating whether it came
    from the curriculum row or from this harness bootstrap. Raises
    `TaskEnvironmentError` on install failure rather than falling back to the
    project `.venv`, where the runner may be absent or the wrong version.
    """
    records: list[dict] = []
    if task_pytest_available(task_py):
        return [{"package": m, "source": CURRICULUM} for m in HARNESS_EXECUTION_PREREQS]
    for mod in HARNESS_EXECUTION_PREREQS:
        proc = subprocess.run(
            [str(task_py), "-m", "pip", "install", mod],
            capture_output=True, text=True, timeout=900,
        )
        if proc.returncode != 0:
            raise TaskEnvironmentError(
                f"harness could not install required test runner {mod!r} into the "
                f"task venv ({task_py}): exit={proc.returncode}\n"
                f"{(proc.stderr or proc.stdout or '')[-800:]}"
            )
        records.append({"package": mod, "source": HARNESS})
    return records


def task_exec_plan(
    inst: dict,
    task_py: Path | str,
) -> dict:
    """Resolve the full, provenance-visible execution plan for one curriculum row.

    Returns interpreter launcher, install steps, execution-prerequisite provenance,
    and the test argv. Nothing here mutates curriculum truth and nothing falls back
    to the project interpreter.
    """
    launcher = resolve_task_python(inst or {})
    install_steps = [s.strip() for s in (inst or {}).get("install") or [] if s.strip()]
    return {
        "instance_id": (inst or {}).get("instance_id", ""),
        "base_image_name": (inst or {}).get("base_image_name", ""),
        "interpreter": launcher,
        "task_py": str(task_py),
        "install_steps": install_steps,
        "install_argv": [task_install_argv(task_py, s) for s in install_steps],
        "execution_prereqs": list(HARNESS_EXECUTION_PREREQS),
    }

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
    inst: dict | None = None,
) -> tuple[bool, str]:
    """Run evaluator against buggy code to verify it detects the bug.

    Returns (all_pass, output).
    Expected: Case A FAIL, Case B FAIL, Case C PASS.
    """
    test_env = {k: v for k, v in os.environ.items()
                if k not in ("PYTHONPATH", "PYTHONHOME")}

    # Resolve the task interpreter through the curriculum row's
    # `base_image_name`. Fails closed rather than using the project `.venv`.
    launcher = resolve_task_python(inst or {})
    task_py = _task_python_path(launcher)

    # `test_cmd` from the curriculum begins with a bare `pytest`; `_test_cmd`
    # rewrites it to `<task python> -m pytest ...` so the task environment's
    # pytest is used instead of whatever is on ambient PATH.
    argv = task_test_argv(task_py, test_cmd)
    p = subprocess.run(
        argv,
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
    ap.add_argument("--out", default="")
    ap.add_argument("--invocation-id", default="",
                    help="Unique invocation ID for result file naming. "
                         "If empty, auto-generated from UUID4.")
    ap.add_argument("--evaluator-dir", default="",
                    help="Directory containing evaluator tests (must be outside target workspace)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run pre-flight checks and evaluator sanity without invoking the model")
    ap.add_argument("--behavioral-prompt", action="store_true",
                    help="Use behavioral prompt (no exact fix disclosure)")
    ap.add_argument("--fresh-arm-workspace", action="store_true",
                    help="Establish HEAD==base_commit plus exactly the authorized "
                         "--test-patch BEFORE the cleanliness pre-flight runs, so an "
                         "arm can start from a known state and re-establish it after a "
                         "previous arm dirtied the tree. Default OFF: flag-off "
                         "behavior is unchanged.")
    args = ap.parse_args()

    instance_id = args.instance_id
    repo = (WORK / instance_id / "repo").resolve()
    evaluator_dir = Path(args.evaluator_dir).resolve() if args.evaluator_dir else None

    # Resolve invocation ID and result path (unique per run)
    invocation_id = args.invocation_id or str(uuid.uuid4())[:16]
    if not args.out:
        result_dir = Path(_HERE) / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(result_dir / f"f1_obs_{invocation_id}.jsonl")
    else:
        out_path = args.out
    print(f"  invocation_id: {invocation_id}")
    print(f"  result_path: {out_path}")

    print("=" * 70)
    print("F1 HARNESS PRE-FLIGHT CHECKS")
    print("=" * 70)

    # --- Fresh-arm workspace preparation (opt-in; runs BEFORE pre-flight) ---
    # The cleanliness pre-flight below rejects ANY dirty tree, but a SWE
    # workspace is SUPPOSED to be dirty: it carries its authorized test patch.
    # Because pre-flight runs before _reset_instance, such a workspace can never
    # reach the reset that would establish the state. When explicitly enabled,
    # establish the authorized state first (HEAD == base_commit, tree == base +
    # exactly the authorized patch), fail-closed, so the pre-flight below
    # verifies it instead of merely rejecting it.
    if args.fresh_arm_workspace:
        print("\n[0/6] Preparing fresh arm workspace...")
        if not args.test_patch:
            print("  FAIL: --fresh-arm-workspace requires --test-patch")
            print("\nABORT: fresh-arm workspace preparation failed.")
            return 2
        patch_path = Path(args.test_patch)
        try:
            from qwen_train.arm_workspace import (
                FreshArmWorkspaceError,
                prepare_fresh_arm_workspace,
            )

            expected_paths = prepare_fresh_arm_workspace(
                repo, args.base_commit, patch_path
            )
        except Exception as exc:  # noqa: BLE001 - fail closed, never continue
            print(f"  FAIL: {exc}")
            print("\nABORT: fresh-arm workspace preparation failed.")
            return 2
        print(f"  PASS: HEAD == {args.base_commit}")
        print(f"  PASS: authorized patch paths: {sorted(expected_paths)}")
        print(f"  PASS: external patch preserved at {patch_path}")

    # --- Pre-flight: Target workspace ---
    print("\n[1/6] Checking target workspace...")
    if args.fresh_arm_workspace:
        from qwen_train.arm_workspace import (
            FreshArmWorkspaceError,
            preflight_target_state_with_patch,
        )

        try:
            target_errors = preflight_target_state_with_patch(
                repo, args.base_commit, Path(args.test_patch)
            )
        except FreshArmWorkspaceError as exc:
            target_errors = [f"Target pre-flight failed: {exc}"]
    else:
        target_errors = _preflight_target_state(repo, args.base_commit)
    if target_errors:
        for err in target_errors:
            print(f"  FAIL: {err}")
        print("\nABORT: Target workspace pre-flight failed.")
        return 2
    print(f"  PASS: Target at {repo}")
    print(f"  PASS: HEAD = {args.base_commit}")
    if args.fresh_arm_workspace:
        print(f"  PASS: Working tree == base + authorized test patch")
    else:
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
        "base_image_name": _pool_env_meta(instance_id).get("base_image_name", ""),
        "install": _pool_env_meta(instance_id).get("install", ""),
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
            evaluator_dir, repo, py, args.test_cmd, inst
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
        # Resolve the task interpreter from the curriculum row's
        # `base_image_name` and build argv explicitly. The previous
        # `args.test_cmd.replace("python -m pytest", ...)` + `shell=True` was
        # inert for every curriculum command (they begin with bare `pytest`), so
        # the runner silently used the project interpreter and ambient PATH.
        launcher = resolve_task_python(inst)
        task_py = _task_python_path(launcher)
        argv = task_test_argv(task_py, args.test_cmd)
        p = subprocess.run(
            argv,
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

    # --- Start F1-owned backend with correct workspace root (replaces health gate) ---
    print("\n[STARTING F1 BACKEND]")
    try:
        import importlib
        f1i = importlib.import_module("f1_infra")
        # FAIL CLOSED on a stale port occupant. A pre-existing backend owns a
        # different SWARM_WORKSPACE_ROOT, so the observation would run against
        # a tree it is not attributed to. F1 does not terminate it: that process
        # is not F1-owned, and killing an operator's dev backend is a
        # destructive action this harness has no authority to take.
        stale_pid = f1i._check_port_listening(8000)
        if stale_pid:
            print(f"  ABORT: port 8000 already occupied by PID {stale_pid} (not F1-owned)")
            print("  Stop the pre-existing backend (e.g. start-dev.ps1) and re-run.")
            _classification = f1i.classify_observation(f1i.RawObservation(
                infra_invalid_reason="process_identity_unknown",
            ))
            _obs_result = {
                "ts": f1i._now_iso(),
                "id": instance_id,
                "verdict": False,
                "verify_reason": "stale_backend_not_owned",
                "interpretation": _classification.to_dict(),
            }
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_obs_result) + "\n")
            print(json.dumps(_obs_result, indent=2))
            return 2

        # Start fresh F1-owned backend with the observation's workspace root
        _backend_record, _evidence_dir = f1i.start_backend_fresh(
            attempt_id=instance_id,
            workspace_root=str(repo),
            port=8000,
            timeout=60,
        )
        _launched_pid = _backend_record.expected_pid
        print(f"  Started F1-owned backend PID={_launched_pid}")
        _backend_pid = _backend_record.pid

        # Resolve the actual listener ONLY if it is F1's own process tree.
        # A foreign PID is never substituted into F1 state.
        print("  Resolving listening PID (owned processes only)...")
        import time
        for _ in range(30):  # up to 30s
            time.sleep(1)
            actual_pid = f1i._check_port_listening(8000)
            if actual_pid and f1i._pid_is_owned_child(actual_pid, _launched_pid):
                if actual_pid != _backend_record.pid:
                    print(f"  Adopted owned descendant PID={actual_pid}")
                    _backend_record.pid = actual_pid
                    _backend_pid = actual_pid
                break
            elif actual_pid:
                print(f"  ABORT: port 8000 served by unowned PID {actual_pid}")
                return 2
            elif actual_pid == _backend_record.pid:
                break

        # Wait for backend to become healthy (includes workspace_match check)
        print("  Waiting for backend health gate...")
        _gate = f1i.wait_for_backend(
            _backend_record, port=8000, timeout=120,
            expected_workspace_root=str(repo),
        )
        if not _gate.passed:
            print(f"  HEALTH GATE FAILED — ABORTING OBSERVATION")
            print(f"  Errors: {_gate.errors}")
            _classification = f1i.classify_observation(f1i.RawObservation(
                infra_invalid_reason="workspace_mismatch"
                if _gate.errors and any(
                    "workspace_mismatch" in e for e in _gate.errors)
                else "health_gate_failure",
            ))
            _obs_result = {
                "ts": f1i._now_iso(),
                "id": instance_id,
                "verdict": False,
                "verify_reason": "health_gate_failure",
                "interpretation": _classification.to_dict(),
            }
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_obs_result) + "\n")
            print(json.dumps(_obs_result, indent=2))
            # Clean up ONLY the process tree F1 launched.
            try:
                import psutil
                if _backend_record.pid and f1i._pid_is_owned_child(
                    _backend_record.pid, _launched_pid
                ):
                    p = psutil.Process(_backend_record.pid)
                    if p.is_running():
                        p.terminate()
                        p.wait(timeout=5)
                        print(f"  [BACKEND] Terminated F1-owned PID={_backend_record.pid}")
                elif _backend_record.pid:
                    print(f"  [BACKEND] SKIP termination: PID {_backend_record.pid} "
                          f"is not owned by F1 launch {_launched_pid}")
            except Exception:
                pass
            return 2
        print("  HEALTH GATE PASSED")
        print(f"  workspace_match: {_gate.workspace_match}")
        print(f"  workspace_root: {_gate.workspace_identity}")
        # Defence in depth: the gate already refuses a mismatch, so reaching
        # here with workspace_match False means the gate was bypassed.
        if not _gate.workspace_match:
            print("  ABORT: workspace identity mismatch after gate pass")
            return 2

    except Exception as _gate_exc:
        print(f"  Health gate import/probe failed: {_gate_exc}")
        print("  HEALTH GATE FAILED — ABORTING OBSERVATION")
        return 2

    # --- Start continuous runtime monitoring (Priority 1) ---
    _evidence_dir = f1i.EVIDENCE_DIR / instance_id
    _evidence_dir.mkdir(parents=True, exist_ok=True)
    _monitor = f1i.RuntimeMonitor(_backend_record, port=8000, router_port=8080)
    _monitor.start()
    print(f"  [MONITOR] Liveness monitoring active (PID {_backend_record.pid})")

    try:
        res = rc._attempt_once(item, args.timeout, allow_approval=True, record=False)
    finally:
        rc._revoke_offline()

    # --- Stop monitoring and classify ---
    _monitor.stop()
    _obs_end_time = f1i._now_iso()

    # Load ATIF step records from the authoritative trajectory file.
    # The trajectory file is named by run_id and contains the real ATIF
    # step records needed for endpoint detection.  res["tools_used"] only
    # carries string tool names and is insufficient for endpoint scanning.
    _atif_steps = []
    _run_ids = res.get("run_ids") or []
    if _run_ids:
        _traj_dir = Path(__file__).resolve().parent.parent / "data" / "trajectories"
        for rid in _run_ids:
            _traj_file = _traj_dir / f"{rid}.jsonl"
            if _traj_file.exists():
                with open(_traj_file, encoding="utf-8") as _tf:
                    for _line in _tf:
                        _line = _line.strip()
                        if not _line:
                            continue
                        _d = json.loads(_line)
                        if _d.get("record_type") == "step":
                            for _tc in _d.get("tool_calls", []):
                                _tc.setdefault("extra", {})["step_id"] = _d.get("step_id", 0)
                                _atif_steps.append(_tc)

    # Build raw observation from CLI result
    _raw_obs = f1i.RawObservation(
        backend_pid=_backend_pid,
        expected_pid=_backend_pid,
        start_time=_backend_record.start_time,
        end_time=_obs_end_time,
        pid_alive=_monitor.alive,
        process_state="alive" if _monitor.alive else "dead",
        cli_ok=bool(res.get("ok")),
        timed_out=bool(res.get("timed_out")),
        elapsed_s=round(float(res.get("elapsed_s", 0)), 1),
        tool_calls=_atif_steps if _atif_steps else res.get("tools_used", []),
        workspace_identity=str(repo),
    )

    # If monitor detected death, override with infra-invalid
    if not _monitor.alive and not _raw_obs.infra_invalid_reason:
        _raw_obs.infra_invalid_reason = "backend_exit"

    # Machine-enforced classification (RULES A-G)
    _classification = f1i.classify_observation(_raw_obs)
    res["interpretation"] = _classification.to_dict()

    # Use the health gate result from wait_for_backend for the manifest
    _manifest = f1i.build_manifest(
        instance_id, _backend_record, _gate, _evidence_dir,
        observation=_raw_obs, classification=_classification,
        monitor=_monitor, invocation_id=invocation_id,
    )
    # Record tooling provenance: which version of the read-before-write guard was active
    if _manifest.provenance:
        _manifest.provenance.tooling_version = "path_equiv_v1"  # _norm() SWARM_WORKSPACE_ROOT fallback
    _manifest_path = f1i.save_manifest(_manifest, _evidence_dir)
    print(f"  [EVIDENCE] Manifest persisted: {_manifest_path}")

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
        "invocation_id": _manifest.evidence_identity.invocation_id if _manifest.evidence_identity else None,
        "manifest_path": str(_manifest_path),
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

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(res) + "\n")
    print(json.dumps(res, indent=2))
    print(f"VERDICT: ok={ok} reason={reason}")

    # --- Cleanup: Stop monitor and terminate the F1-owned backend ---
    try:
        if "_monitor" in locals() and _monitor:
            _monitor.stop()
            print("  [MONITOR] Stopped")
    except Exception as e:
        print(f"  [MONITOR] Stop error: {e}")

    try:
        if "_backend_record" in locals() and _backend_record and _backend_record.pid:
            # Ownership check: never terminate a PID that F1 did not launch.
            # R1 EJ-R1-001 killed a pre-existing dev backend here purely
            # because its PID had been substituted into F1 state.
            if "_launched_pid" in locals() and not f1i._pid_is_owned_child(
                _backend_record.pid, _launched_pid
            ):
                print(f"  [BACKEND] SKIP termination: PID {_backend_record.pid} "
                      f"is not F1-owned (launched {_launched_pid})")
            else:
                import psutil
                p = psutil.Process(_backend_record.pid)
                if p.is_running():
                    p.terminate()
                    p.wait(timeout=5)
                    print(f"  [BACKEND] Terminated F1-owned PID={p.pid}")
                else:
                    print(f"  [BACKEND] PID={p.pid} already stopped")
    except Exception as e:
        print(f"  [BACKEND] Cleanup error: {e}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
