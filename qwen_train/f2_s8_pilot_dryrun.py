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

Dedicated uv execution (R1-B)
-----------------------------
A ``uv run pytest`` task's environment is created by uv.  The dry run used to
build one shell string (``"<declared cmd> --junitxml=<path>"``) and run it with
``shell=True``, so ``uv`` was resolved from the ambient ``PATH`` -- which on the
acquisition host is the owner's profile-level ``uv.exe`` rather than the
dedicated, hash-verified builder tool.  That is a substitution the manifest
could not see.  A uv-owned command is now tokenised, its executable replaced
with the verified ``C:\\f2_pilot\\tools\\uv.exe``, run as an argument list, and
given a PATH from which every other directory holding a ``uv`` binary has been
removed.  A command that cannot be expressed as an argument list (shell
metacharacters) is refused rather than executed through a shell.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from qwen_train.f2_s8_acquisition import require_unprivileged_identity
from qwen_train.f2_s8_pilot_build import (
    _sha256_file,
    declared_test_cmd,
    resolve_and_verify_uv,
    uses_uv_environment,
)

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

#: Tokens that require a shell to interpret.  A declared command containing one
#: cannot be executed as an argument list, so the dedicated-uv guarantee cannot
#: be made and the dry run refuses instead of silently falling back to a shell.
_SHELL_METACHARACTERS = ("&&", "||", "|", ">", "<", ";", "`", "$(", "${")

#: File names an ambient uv installation can present as on Windows and POSIX.
_UV_EXECUTABLE_NAMES = ("uv", "uv.exe", "uv.cmd", "uv.bat") if os.name == "nt" else ("uv",)


class UvExecutionError(RuntimeError):
    """A uv-owned command cannot be run through the approved executable."""


def _split_declared_command(test_cmd: str) -> list[str]:
    """Tokenise a declared command, refusing anything a shell would interpret.

    ``shlex`` with ``posix=False`` keeps backslashes in Windows paths intact.
    Fully quoted tokens are unquoted, because the argv list no longer needs the
    shell's quoting (and passing the quotes through would break the path).
    """
    text = str(test_cmd or "").strip()
    if not text:
        raise UvExecutionError("UV_EXECUTION_UNSAFE_COMMAND: empty command")
    for marker in _SHELL_METACHARACTERS:
        if marker in text:
            raise UvExecutionError(
                "UV_EXECUTION_UNSAFE_COMMAND: the declared command contains "
                f"{marker!r}, so it cannot be executed as an argument list and "
                "the dedicated uv executable cannot be guaranteed")
    tokens = shlex.split(text, posix=False)
    cleaned: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
            cleaned.append(token[1:-1])
        else:
            cleaned.append(token)
    return cleaned


def _directory_has_ambient_uv(directory: Path) -> bool:
    try:
        return any((directory / name).is_file() for name in _UV_EXECUTABLE_NAMES)
    except OSError:
        return False


def uv_safe_path(path_value: str, uv_exe: Path) -> list[str]:
    """``PATH`` entries that cannot resolve a uv other than ``uv_exe``.

    Every directory that holds a uv binary is dropped unless it is the approved
    executable's own directory, and the approved directory is placed first.
    This is defence in depth: the command is also an argument list, so PATH is
    not consulted for the executable at all -- but a task that shells out to
    ``uv`` internally still gets the approved one.
    """
    approved_dir = Path(uv_exe).parent
    approved_key = approved_dir.resolve().as_posix().lower()
    kept: list[str] = [str(approved_dir)]
    for raw in str(path_value or "").split(os.pathsep):
        entry = raw.strip()
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve().as_posix().lower()
        except OSError:
            resolved = Path(entry).as_posix().lower()
        if resolved == approved_key:
            continue
        if _directory_has_ambient_uv(Path(entry)):
            continue
        kept.append(entry)
    return kept


def prepare_uv_execution(
    test_cmd: str, *, uv_path: Path | str | None = None,
    expected_sha256: str | None = None,
    fixture_root: Path | str | None = None,
) -> dict:
    """The exact argv and identity for a uv-owned declared command.

    Returns ``{"argv", "uv_path", "uv_sha256", "ambient_uv_removed"}``.  Raises
    when the declared command is not uv-led, when the approved executable is
    absent or corrupt (:func:`resolve_and_verify_uv`), or when the command needs
    a shell.  ``expected_sha256`` exists for tests; production must pass
    ``None`` so the module's approved digest is enforced.
    """
    uv_exe = resolve_and_verify_uv(uv_path, expected_sha256,
                                   fixture_root=fixture_root)
    argv = _split_declared_command(test_cmd)
    if not argv:
        raise UvExecutionError("UV_EXECUTION_UNSAFE_COMMAND: empty command")
    head = Path(argv[0]).name.lower()
    if head not in _UV_EXECUTABLE_NAMES:
        raise UvExecutionError(
            "UV_EXECUTION_UNSAFE_COMMAND: the declared command does not start "
            f"with uv (starts with {argv[0]!r}); refusing to substitute")
    return {
        "argv": [str(uv_exe), *argv[1:]],
        "uv_path": str(uv_exe),
        "uv_sha256": _sha256_file(uv_exe),
        "ambient_uv_removed": True,
    }


def run_test(*, python: str, repo: Path, test_cmd: str, junit: Path,
             timeout: int, uv_offline: bool = False,
             uv_path: Path | str | None = None,
             uv_sha256: str | None = None,
             fixture_root: Path | str | None = None) -> dict:
    """Run the DECLARED test command with --junitxml appended, as the guest
    executor will. Nothing is rewritten: the command is the population's.

    The declared commands invoke ``pytest`` bare, so the workspace's own
    ``Scripts`` directory is prepended to PATH. Without that the shell would
    resolve a different pytest (or none) and report an environment failure as
    if it were the task's.

    ``uv_offline`` marks a uv-OWNED task: its command runs through the dedicated,
    hash-verified uv as an argument list (R1-B) and with ``UV_OFFLINE=1``.
    """
    if "--junitxml" in test_cmd:
        raise ValueError("declared test_cmd already carries --junitxml")
    # Fail closed at the UNTRUSTED boundary: this runs the task's own test
    # command, which imports and executes repository code.
    require_unprivileged_identity("qwen_train.f2_s8_pilot_dryrun.run_test")
    env = dict(os.environ)
    # ``python`` is None for uv-owned tasks: there is no workspace Scripts
    # directory to prepend, and ``uv run`` supplies the environment itself.
    prefix = [p for p in (str(Path(python).parent) if python else "", POSIX_BIN) if p]
    env["PATH"] = os.pathsep.join(prefix + [env.get("PATH", "")])
    t0 = time.time()

    if uv_offline:
        plan = prepare_uv_execution(test_cmd, uv_path=uv_path,
                                    expected_sha256=uv_sha256,
                                    fixture_root=fixture_root)
        env["UV_OFFLINE"] = "1"
        env["PATH"] = os.pathsep.join(
            uv_safe_path(env.get("PATH", ""), Path(plan["uv_path"])))
        argv = [*plan["argv"], f"--junitxml={junit}"]
        proc = subprocess.run(
            argv, cwd=repo, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        execution = {"mode": "argv", "argv": argv,
                     "uv_path": plan["uv_path"],
                     "uv_sha256": plan["uv_sha256"]}
    else:
        cmd = f"{test_cmd} --junitxml={junit}"
        # The declared command is a shell string; run it through the shell
        # exactly as the guest executor does, so any quoting or PATH assumption
        # surfaces here rather than in the guest.  This path never invokes uv.
        proc = subprocess.run(
            cmd, shell=True, cwd=repo, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        execution = {"mode": "shell", "command": cmd}

    return {
        "exit_status": proc.returncode,
        "elapsed_s": int(time.time() - t0),
        "junit_exists": junit.is_file(),
        "junit_bytes": junit.stat().st_size if junit.is_file() else 0,
        "execution": execution,
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
