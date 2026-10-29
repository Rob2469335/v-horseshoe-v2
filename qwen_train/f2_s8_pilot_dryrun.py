"""F2 S8 pilot: host-side dry run of each built workspace.

Purpose: prove each workspace's interpreter, dependencies and declared test
command actually work BEFORE the workspaces are delivered to the isolated
guest. A workspace that cannot run its own test command offline is an
acquisition failure, not a scientific result, and finding that on the host --
where a diagnostic is cheap -- is the point.

This runs real task code and produces real JUnit. It is NOT pilot evidence:
the pilot's evidence is what the governed pipeline records from the guest
under the trusted store. These files are scratch, kept only to explain a
later failure.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from qwen_train.f2_s8_acquisition import require_unprivileged_identity
from qwen_train.f2_s8_pilot_build import declared_test_cmd, uses_uv_environment

ROOT = Path(r"C:\f2_pilot")
INPUTS = Path(r"C:\Users\rober\F2_EVIDENCE\s8\pilot_inputs\full")
DRY = Path(r"C:\Users\rober\F2_EVIDENCE\s8\pilot_build\dryrun")

#: Tasks whose declared command is ``uv run pytest``. uv must be told the
#: environment is complete and the network is forbidden, or it will try to
#: re-resolve against PyPI and fail inside the offline guest.  This table is an
#: OVERRIDE only; uv-ownership is derived from the DECLARED command exactly as
#: the builder derives it (``uses_uv_environment``), so a task added later is
#: handled by the same rule that governs its environment.
UV_ALL_EXTRAS = ("openai__openai-agents-python-1633",
                 "openai__openai-agents-python-1636")

#: POSIX ``tee``, required at import time by the openai-agents MCP test
#: helpers. Its absence aborts collection for the whole session.
POSIX_BIN = r"C:\Program Files\Git\usr\bin"


def run_test(*, python: str, repo: Path, test_cmd: str, junit: Path,
             timeout: int, uv_offline: bool = False) -> dict:
    """Run the DECLARED test command with --junitxml appended, as the guest
    executor will. Nothing is rewritten: the command is the population's.

    The declared commands invoke ``pytest`` bare, so the workspace's own
    ``Scripts`` directory is prepended to PATH. Without that the shell would
    resolve a different pytest (or none) and report an environment failure as
    if it were the task's.
    """
    import os

    if "--junitxml" in test_cmd:
        raise ValueError("declared test_cmd already carries --junitxml")
    # Fail closed at the UNTRUSTED boundary: this runs the task's own test
    # command, which imports and executes repository code.
    require_unprivileged_identity("qwen_train.f2_s8_pilot_dryrun.run_test")
    cmd = f"{test_cmd} --junitxml={junit}"
    env = dict(os.environ)
    # ``python`` is None for uv-owned tasks: there is no workspace Scripts
    # directory to prepend, and ``uv run`` supplies the environment itself.
    prefix = [p for p in (str(Path(python).parent) if python else "", POSIX_BIN) if p]
    env["PATH"] = os.pathsep.join(prefix + [env.get("PATH", "")])
    if uv_offline:
        env["UV_OFFLINE"] = "1"
    t0 = time.time()
    # The declared command is a shell string; run it through the shell exactly
    # as the guest executor does, so any quoting or PATH assumption surfaces
    # here rather than in the guest.
    proc = subprocess.run(
        cmd, shell=True, cwd=repo, capture_output=True, text=True,
        timeout=timeout, env=env,
    )
    return {
        "exit_status": proc.returncode,
        "elapsed_s": int(time.time() - t0),
        "junit_exists": junit.is_file(),
        "junit_bytes": junit.stat().st_size if junit.is_file() else 0,
        "stdout_tail": proc.stdout[-1500:],
        "stderr_tail": proc.stderr[-1500:],
    }


def main(argv=None) -> int:
    require_unprivileged_identity("qwen_train.f2_s8_pilot_dryrun")
    ap = argparse.ArgumentParser()
    ap.add_argument("task_ids", nargs="*")
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--out", default=str(DRY / "dryrun.json"))
    args = ap.parse_args(argv)

    manifest = json.loads((ROOT / "build_manifest.json").read_text(encoding="utf-8"))
    ids = args.task_ids or [k for k, v in manifest.items() if v.get("status") == "built"]
    DRY.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}

    for task_id in ids:
        entry = manifest.get(task_id)
        if not entry or entry.get("status") != "built":
            results[task_id] = {"status": "not_built"}
            continue
        spec = json.loads((INPUTS / f"{task_id}.json").read_text(encoding="utf-8"))
        # The DECLARED command the registration and the guest bind, via the same
        # canonical helper the builder and the pilot use -- never the raw
        # ``test_cmds[0]``.  A SWE-bench-Live row declares ``test_cmds`` as a
        # LIST and the frozen registration binds the " && " chain the source
        # adapter derives from it (see ``f2_s8_pilot_live.build_specs``), so a
        # raw ``[0]`` would run a command the session never runs and make this
        # host pre-delivery check validate a different command than the guest.
        test_cmd = declared_test_cmd(spec)
        print(f"DRYRUN {task_id} :: {test_cmd}", flush=True)

        task_out: dict[str, dict] = {}
        for side in ("base", "gold"):
            # Each side runs against ITS OWN environment. Using the base venv
            # for gold would import base source through the editable .pth and
            # make a correct gold patch look like a failure.
            repo_key = "repo" if side == "base" else "gold_repo"
            py_key = "python" if side == "base" else "gold_python"
            python = entry.get(py_key) or entry["python"]
            junit = DRY / f"{task_id}.{side}.junit.xml"
            if junit.exists():
                junit.unlink()
            try:
                task_out[side] = run_test(
                    python=python, repo=Path(entry[repo_key]), test_cmd=test_cmd,
                    junit=junit, timeout=args.timeout,
                    # uv-ownership comes from the DECLARED command (the same
                    # rule the builder uses); the explicit table stays as an
                    # override so the pilot's behaviour cannot drift.
                    uv_offline=(task_id in UV_ALL_EXTRAS
                                or uses_uv_environment(test_cmd)),
                )
            except subprocess.TimeoutExpired:
                task_out[side] = {"status": "timeout", "exit_status": None,
                                  "junit_exists": junit.exists()}
            except Exception as exc:  # noqa: BLE001
                task_out[side] = {"status": f"{type(exc).__name__}: {exc}",
                                  "exit_status": None, "junit_exists": junit.exists()}
            print(f"  {side}: exit={task_out[side].get('exit_status')} "
                  f"junit={task_out[side].get('junit_bytes')} "
                  f"{task_out[side].get('elapsed_s', '?')}s", flush=True)

        # Evaluate both sides with the authoritative evaluator so the host dry
        # run reports the same base/gold relationship the pilot will measure.
        from qwen_train import f2_evaluator as V

        verdicts = {}
        for side in ("base", "gold"):
            junit = DRY / f"{task_id}.{side}.junit.xml"
            if not junit.is_file():
                verdicts[side] = "no_junit"
                continue
            try:
                out = DRY / f"{task_id}.{side}.report.json"
                V.produce_report(
                    junit_path=junit, fail_to_pass=spec["FAIL_TO_PASS"],
                    pass_to_pass=spec["PASS_TO_PASS"], out_path=out,
                    instance_id=task_id, repository=spec["repo"],
                    base_commit=spec["base_commit"], execution_state_identity=side,
                )
                payload = V.parse_report(out.read_bytes())
                verdict, why = V.verdict_from_report_payload(payload)
                verdicts[side] = f"{verdict} ({why})"
            except Exception as exc:  # noqa: BLE001
                verdicts[side] = f"evaluator_error: {type(exc).__name__}: {exc}"
        print(f"  base={verdicts['base']}", flush=True)
        print(f"  gold={verdicts['gold']}", flush=True)
        task_out["verdicts"] = verdicts
        task_out["status"] = "dryrun_complete"
        results[task_id] = task_out

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=1, sort_keys=True),
                              encoding="utf-8")
    print(json.dumps({k: v.get("status") for k, v in results.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
