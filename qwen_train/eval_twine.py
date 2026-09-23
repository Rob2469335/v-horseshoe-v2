"""Future Twine evaluation harness with learning bridge integration.

This is the ACTIVE evaluator for future Twine evaluations. It preserves the
exact validated execution semantics of the frozen N1 (qwen_train/run_twine_eval.py)
while adding:

- Canonical rollout identity (uuid4 per invocation)
- Harness credential propagation (SWARM_TASK_ID / SWARM_HARNESS_KEY / SWARM_ROLLOUT_ID)
- Learning bridge integration (build_and_submit_evaluation_failure)

The frozen N1 artifact (run_twine_eval.py) must NOT be modified.

Usage:
    python qwen_train/eval_twine.py \\
        --instance-id pypa__twine-1066 \\
        --base-commit 4a1fc064a7899872ee845df6a8810bb51a6845ac \\
        --test-patch <path-to-test.patch>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import cli_baseline_swe as cls
import run_curriculum as rc
import swe_rebench_probe as probe
from swe_rebench_probe import _run, _test_cmd, _f2p_result


def main() -> int:
    ap = argparse.ArgumentParser(description="Twine evaluation with learning bridge")
    ap.add_argument("--instance-id", default="pypa__twine-1066")
    ap.add_argument("--base-commit", default="4a1fc064a7899872ee845df6a8810bb51a6845ac")
    ap.add_argument("--test-cmd", default=(
        "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider "
        "-W ignore::DeprecationWarning tests/test_package.py"
    ))
    ap.add_argument("--f2p", action="append", default=[
        "tests/test_package.py::test_metadata_dictionary_keys",
        "tests/test_package.py::test_metadata_dictionary_values[None]",
        "tests/test_package.py::test_metadata_dictionary_values[gpg_signature1]",
    ])
    ap.add_argument("--problem-statement", default="")
    ap.add_argument("--test-patch", default="")
    ap.add_argument("--timeout", type=int, default=1200)
    args = ap.parse_args()

    instance_id = args.instance_id
    base_commit = args.base_commit
    f2p = args.f2p

    # --- Identity: canonical rollout ID, generated fresh per invocation ---
    rollout_id = str(uuid.uuid4())

    # Load problem statement and test patch
    ps_text = ""
    if args.problem_statement and Path(args.problem_statement).exists():
        ps_text = Path(args.problem_statement).read_text(encoding="utf-8")
    tp_text = ""
    if args.test_patch and Path(args.test_patch).exists():
        tp_text = Path(args.test_patch).read_text(encoding="utf-8")

    inst = {
        "instance_id": instance_id,
        "base_commit": base_commit,
        "test_cmd": args.test_cmd,
        "fail_to_pass": f2p,
        "pass_to_pass": [],
        "split": "repair",
    }
    hf_inst = {"problem_statement": ps_text, "test_patch": tp_text}

    # --- Step 1: Reset repo ---
    print("=" * 60)
    print("STEP 1: Reset repo to base + apply test_patch")
    print("=" * 60)
    src = cls._reset_instance(inst, hf_inst)
    cls._clean_shadowing_metadata(src)
    print(f"  repo: {src}")
    print(f"  HEAD: {probe._run(['git', 'rev-parse', 'HEAD'], src)[1].strip()}")

    # --- Step 2: Baseline F2P ---
    print()
    print("=" * 60)
    print("STEP 2: Verify baseline FAILS (test_patch applied, source buggy)")
    print("=" * 60)
    py = src / ".venv" / "Scripts" / "python.exe"
    cmd = _test_cmd(py, args.test_cmd)
    rc_code, baseline_output = _run(cmd, src, timeout=120)
    print(f"  pytest exit code: {rc_code}")
    f2p_p, f2p_f = _f2p_result(baseline_output, f2p)
    print(f"  F2P: {f2p_p} passed, {f2p_f} failed")
    if f2p_f < len(f2p):
        print("  ERROR: baseline did not fail as expected!")
        return 1
    print("  BASELINE CONFIRMED")

    # --- Step 3: Run agent with harness credentials ---
    print()
    print("=" * 60)
    print("STEP 3: Run robs4b repair agent")
    print("=" * 60)
    os.environ["SWARM_WORKSPACE_ROOT"] = str(src)
    os.environ["SWARM_TASK_ID"] = instance_id
    os.environ["SWARM_HARNESS_KEY"] = os.environ.get("SWARM_HARNESS_KEY", "dev")
    os.environ["SWARM_ROLLOUT_ID"] = rollout_id

    from swarm_os.services.trust_ledger import grant
    for scope in ("filesystem", "sandbox_repl", "mcp", "web_fetch"):
        grant(scope, 8 * 3600)

    item = {
        "id": instance_id,
        "prompt": cls._build_prompt(inst, hf_inst),
        "split": "repair",
    }

    t0 = time.time()
    try:
        res = rc._attempt_once(item, args.timeout, allow_approval=True, record=False)
    finally:
        rc._revoke_offline()
        os.environ.pop("SWARM_WORKSPACE_ROOT", None)
        os.environ.pop("SWARM_TASK_ID", None)
        os.environ.pop("SWARM_HARNESS_KEY", None)
        os.environ.pop("SWARM_ROLLOUT_ID", None)

    elapsed = time.time() - t0

    # --- Step 4: Post-repair F2P ---
    print()
    print("=" * 60)
    print("STEP 4: Verify post-repair tests")
    print("=" * 60)
    cls._clean_shadowing_metadata(src)
    cmd = _test_cmd(py, args.test_cmd)
    rc_code2, after_output = _run(cmd, src, timeout=120)
    f2p_p2, f2p_f2 = _f2p_result(after_output, f2p)
    print(f"  F2P: {f2p_p2} passed, {f2p_f2} failed")

    ok, reason = cls._test_result(after_output, f2p, [], set())
    print(f"  verdict: ok={ok} reason={reason}")

    # --- Step 5: Git diff ---
    diff_stat = probe._run(
        ["git", "diff", "--stat", base_commit, "HEAD"], src
    )[1].strip()

    # --- Step 6: Learning bridge ---
    print()
    print("=" * 60)
    print("STEP 5: Learning bridge")
    print("=" * 60)
    from runtime_v2.api.evaluation_bridge import build_and_submit_evaluation_failure

    bridge_result = asyncio.run(
        build_and_submit_evaluation_failure(
            task_id=instance_id,
            rollout_id=rollout_id,
            res=res,
            f2p_p=f2p_p,
            f2p_f=f2p_f,
            f2p_p2=f2p_p2,
            f2p_f2=f2p_f2,
            diff_stat=diff_stat,
            ok=ok,
            timeout_seconds=args.timeout,
            agent_model="robs4b",
            routing_mode=os.environ.get("SWARM_ROUTING_MODE", "unknown"),
            run_ids=None,
        )
    )
    print(f"  bridge_result: {bridge_result}")

    # --- Summary ---
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    result = {
        "instance_id": instance_id,
        "rollout_id": rollout_id,
        "base_commit": base_commit,
        "baseline_f2p_failed": f2p_f,
        "baseline_f2p_passed": f2p_p,
        "agent_cli_ok": res.get("cli_ok"),
        "agent_timed_out": res.get("timed_out"),
        "agent_tool_order": res.get("tool_order", []),
        "agent_elapsed_s": round(elapsed, 1),
        "post_f2p_passed": f2p_p2,
        "post_f2p_failed": f2p_f2,
        "verdict": ok,
        "verify_reason": reason,
        "diff_stat": diff_stat,
        "bridge_result": bridge_result,
    }
    print(json.dumps(result, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
