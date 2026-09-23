"""Run ONE local repair task through the agent harness, using a LOCAL hf_inst.

Thin adapter (NOT a harness change): reuses `cli_baseline_swe`'s
`_reset_instance` / `_build_prompt` / `_run_tests` / `_test_result` and
`run_curriculum._attempt_once`, but supplies `hf_inst` (problem_statement +
test_patch) from a task record instead of fetching from HuggingFace.

Usage:
    python qwen_train/run_repair_task.py \
        --instance-id repair_task1 \
        --base-commit 3127b3a1... \
        --problem-statement "..." \
        --test-cmd "python -m pytest tests/test_repair_task1.py -q" \
        --f2p tests/test_repair_task1.py::test_subfolder_write_root_does_not_cover_workspace

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
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import run_curriculum as rc  # noqa: E402
import cli_baseline_swe as cls  # noqa: E402
from swe_rebench_probe import WORK  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--base-commit", required=True)
    ap.add_argument("--problem-statement", required=True)
    ap.add_argument("--test-cmd", required=True)
    ap.add_argument("--f2p", action="append", default=[])
    ap.add_argument("--test-patch", default="")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--out", default="qwen_train/results/repair_task.jsonl")
    args = ap.parse_args()

    instance_id = args.instance_id
    repo = (WORK / instance_id / "repo").resolve()
    if not (repo / ".git").exists():
        print(f"FATAL: no repo at {repo}")
        return 2

    # The record the harness reads.
    inst = {
        "instance_id": instance_id,
        "base_commit": args.base_commit,
        "test_cmd": args.test_cmd,
        "fail_to_pass": args.f2p or [],
        "pass_to_pass": [],
        "split": "repair",
    }
    # The external-instance payload the harness would otherwise fetch from HF.
    test_patch_content = Path(args.test_patch).read_text("utf-8") if args.test_patch and Path(args.test_patch).exists() else ""
    hf_inst = {"problem_statement": args.problem_statement, "test_patch": test_patch_content}

    cls._reset_instance(inst, hf_inst)
    f2p = cls.probe._parse_list_field(inst.get("fail_to_pass") or [])

    # The instance clone has NO per-instance venv (unlike the SWE pool). Run pytest
    # directly against the clone with the host venv python, PYTHONPATH cleared so
    # cwd-first resolution imports THIS clone's swarm_os, not the source project.
    py = Path(sys.executable).parent.parent / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    test_env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}

    def _test_once() -> str:
        cmd = args.test_cmd.replace("python -m pytest", f'"{py}" -m pytest')
        p = subprocess.run(
            cmd, shell=True,
            cwd=str(repo), capture_output=True, text=True, timeout=600, env=test_env,
        )
        return (p.stdout or "") + (p.stderr or "")

    base_failing = cls._failing_ids(_test_once())
    base_p2p_fail = set()
    print(f"base: {len([t for t in f2p if t in base_failing])}/{len(f2p)} f2p failing")

    item = {"id": instance_id, "prompt": cls._build_prompt(inst, hf_inst), "split": "repair"}
    os.environ["SWARM_WORKSPACE_ROOT"] = str(repo)
    # Grant task-scoped offline tool access so sandbox_repl/lsp/mcp aren't
    # blocked by the CONFIRM approval gate during unattended runs.
    grant_msg = rc._grant_offline()
    print(f"offline grants: {grant_msg}")
    # Also grant 'mcp' itself — the 4B routes filesystem/sandbox_repl through
    # the mcp dispatch tool instead of calling them directly.
    # And 'web_fetch' — a single CONFIRM block teaches the 4B to avoid ALL
    # non-web_search tools, poisoning the rest of the trajectory.
    try:
        from swarm_os.services.trust_ledger import grant
        grant("mcp", 8 * 3600)
        grant("web_fetch", 8 * 3600)
        grant("filesystem", 8 * 3600)
        print("offline grants: mcp, web_fetch, filesystem (8h)")
    except Exception as exc:
        print(f"grant failed: {exc}")
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

    # True source diff (what the agent actually changed) vs the base commit.
    diff = subprocess.run(
        ["git", "diff", "--stat", args.base_commit, "HEAD"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()
    res["diff_stat"] = diff

    # --- Learning bridge: wire behavioral failures into PromptRepairer ---
    # Uses the shared finalization function from evaluation_bridge.
    # The bridge NEVER modifies the fixture, gold patch, model, or PromptRepairer.
    # rollout_id: generated fresh per invocation (uuid4), not derived from instance_id.
    # run_ids: the CLI-based evaluator path does not establish a reliable
    # association between trajectory files and this evaluation. Pass None
    # (normalized to [] inside build_and_submit).
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
                run_ids=None,
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