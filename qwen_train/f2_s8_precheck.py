"""F2-S8 Stage-1 collection-only pre-check (executable gate).

WHAT THIS IS
------------
A real gate that answers one narrow question for a future authorized batch:

    Can the declared test targets be *collected* in the built base and gold
    environments, with a known collection-error cause?

It is **collection-only**: it never runs a test body, never executes a scored
task, never consumes a grant, never writes a ledger and never awards credit.
The result is bound to the exact revision, manifest, registration, candidate
list, policy and code version it was produced under; a stale or mismatched
binding fails closed.

WHAT IT DOES NOT ESTABLISH
--------------------------
Collection success is **not** scientific admission.  It does not show that the
base tests fail, that the gold tests pass, or that the task is admitted.  The
result says so explicitly.

PASS / FAIL / INCONCLUSIVE
--------------------------
``PASS``          both sides collected every declared target (or the base side
                  had only PROJECT-owned collection errors, which the gold
                  patch is expected to fix)
``FAIL``          an explicit incompatibility: a missing environment dependency,
                  a gold-side project collection failure, no tests collected,
                  or a stale binding
``INCONCLUSIVE``  the gate could not establish the facts: missing venv, timeout,
                  unparsed collection output, a target absent without an error,
                  or an unknown cause.  Fail closed: it never becomes PASS.

ISOLATION
---------
All output goes under ``--out-dir``; the gate refuses an output directory that
overlaps the repository root, either authorization/consumption ledger, the
staging root, or a ``pilot_results*`` directory.  Tests prove the ledgers,
trust root and credit are byte-identical before and after.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from qwen_train.f2_evaluator import changed_module_paths, classify_collection_cause

__all__ = [
    "PRECHECK_SCHEMA",
    "PRECHECK_VERSION",
    "PrecheckError",
    "DEFAULT_POLICY",
    "run_precheck",
    "select_candidates",
    "main",
]

PRECHECK_SCHEMA = "f2-s8-precheck/v1"
PRECHECK_VERSION = "1.0.0"

DEFAULT_POLICY: dict[str, Any] = {
    "max_attempts": 3,
    "max_tasks_per_batch": 10,
    "max_grants_per_session": 5,
    "infra_stop_fraction": 0.10,
    # Code-pinned exclusions; a policy file may repeat but never remove them.
    "excluded_tasks": ["networkx__networkx-8207"],
    # Withheld workspace (P1): not a candidate until its workspace is preserved.
    "parked_tasks": ["stanfordnlp__dspy-8739"],
}

FORBIDDEN_SHELL = ("&&", "||", ";", "|", ">", "<")
RUN_ONLY_FLAGS = {
    "-rA", "-r", "-q", "-v", "-vv", "-vvv", "-x", "--maxfail",
    "--tb=auto", "--tb=long", "--tb=short", "--tb=line", "--tb=native",
    "--tb=no", "--durations",
}

SIDE_PASS = "PASS"
SIDE_FAIL = "FAIL"
SIDE_INCONCLUSIVE = "INCONCLUSIVE"

REASON_REVISION_STALE = "STALE_REVISION"
REASON_MANIFEST_STALE = "STALE_MANIFEST"
REASON_REGISTRATION_STALE = "STALE_REGISTRATION"
REASON_TASK_LIST_STALE = "STALE_TASK_LIST"
REASON_POLICY_STALE = "STALE_POLICY"
REASON_UNREGISTERED = "UNREGISTERED"
REASON_PREVIOUSLY_RECORDED = "PREVIOUSLY_RECORDED"
REASON_RETRY_CAP = "RETRY_CAP_EXHAUSTED"
REASON_EXCLUDED = "EXCLUDED_BY_POLICY"
REASON_PARKED = "PARKED_BY_POLICY"
REASON_NOT_BUILT = "WORKSPACE_NOT_BUILT"
REASON_WORKSPACE_MISSING = "WORKSPACE_MISSING"
REASON_VENV_MISSING = "VENV_MISSING"
REASON_INPUT_RECORD_MISSING = "INPUT_RECORD_MISSING"
REASON_TEST_CMD_UNSUPPORTED = "TEST_CMD_UNSUPPORTED"
REASON_COLLECT_TIMEOUT = "COLLECT_TIMEOUT"
REASON_COLLECT_ERROR = "COLLECT_ERROR"
REASON_ENVIRONMENT_COLLECTION = "ENVIRONMENT_COLLECTION_ERROR"
REASON_UNKNOWN_COLLECTION = "UNKNOWN_COLLECTION_ERROR"
REASON_GOLD_PROJECT_COLLECTION = "GOLD_PROJECT_COLLECTION_ERROR"
REASON_TARGET_NOT_COLLECTED = "TARGET_NOT_COLLECTED"
REASON_NO_TESTS = "NO_TESTS_COLLECTED"
REASON_COLLECT_UNPARSED = "COLLECTION_UNPARSED"


class PrecheckError(RuntimeError):
    """The pre-check cannot be conducted safely (fail closed)."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(payload: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_bytes(payload))


def _git_revision(repo_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrecheckError(f"git rev-parse HEAD failed: {exc}") from exc
    revision = (out.stdout or "").strip()
    if out.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise PrecheckError(
            "repository revision is not a real 40-hex commit id from "
            f"`git rev-parse HEAD` (got {revision!r}, exit {out.returncode})"
        )
    return revision


def _registered_task_ids(path: Path) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise PrecheckError(f"registration is unreadable: {exc}") from exc
    if isinstance(raw, Mapping):
        raw = raw.get("task_ids") or raw.get("tasks") or list(raw)
    if not isinstance(raw, list):
        raise PrecheckError("registration must be a list of task ids")
    out = [str(t) for t in raw]
    if len(set(out)) != len(out):
        raise PrecheckError("registration contains duplicate task ids")
    return out


def _policy(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_POLICY)
    if policy:
        merged.update(policy)
    # The code-pinned exclusions cannot be removed by a policy file.
    for key in ("excluded_tasks", "parked_tasks"):
        pinned = set(DEFAULT_POLICY[key])
        merged[key] = sorted(pinned | {str(t) for t in (merged.get(key) or [])})
    merged["max_attempts"] = int(merged["max_attempts"])
    merged["max_tasks_per_batch"] = int(merged["max_tasks_per_batch"])
    merged["max_grants_per_session"] = int(merged["max_grants_per_session"])
    merged["infra_stop_fraction"] = float(merged["infra_stop_fraction"])
    if merged["max_attempts"] != 3:
        raise PrecheckError("max_attempts is frozen at 3")
    if merged["max_tasks_per_batch"] > 10:
        raise PrecheckError("max_tasks_per_batch may not exceed 10")
    return merged


def _ledger_state(ledger_paths: Sequence[Path]) -> tuple[set[str], set[str], dict[str, int]]:
    """Read-only: recorded tasks, terminal scientific tasks, attempt counts."""
    from qwen_train.f2_s8_factory import AppendOnlyLedger

    recorded: set[str] = set()
    terminal: set[str] = set()
    attempts: dict[str, int] = {}
    for path in ledger_paths:
        ledger = AppendOnlyLedger(path)
        ok, reason = ledger.verify()
        if not ok:
            raise PrecheckError(f"ledger {path} failed verification: {reason}")
        for row in ledger.records():
            task = str(row.get("task_id") or "")
            if not task:
                continue
            recorded.add(task)
            attempts[task] = attempts.get(task, 0) + 1
            if row.get("outcome_class") == "scientific":
                terminal.add(task)
    return recorded, terminal, attempts


def select_candidates(
    *,
    registered: Sequence[str],
    build_manifest: Mapping[str, Any],
    recorded: set[str],
    terminal: set[str],
    attempts: Mapping[str, int],
    policy: Mapping[str, Any],
    requested: Sequence[str] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Return (selected, excluded).  Only BUILT, unrecorded tasks are eligible."""
    excluded: list[dict[str, str]] = []
    built = {
        str(k) for k, v in build_manifest.items()
        if isinstance(v, Mapping) and v.get("status") == "built"
    }
    requested_set = set(requested or [])
    selected: list[str] = []
    for task in registered:
        if requested_set and task not in requested_set:
            continue
        if task in policy["excluded_tasks"]:
            excluded.append({"task_id": task, "reason": REASON_EXCLUDED})
            continue
        if task in policy["parked_tasks"]:
            excluded.append({"task_id": task, "reason": REASON_PARKED})
            continue
        if task not in built:
            # Outside the candidate pool.  Only an explicitly requested task is
            # reported as a specific exclusion; otherwise the registered-unbuilt
            # figure is a count, not 3,800 noisy rows.
            if requested_set:
                excluded.append({"task_id": task, "reason": REASON_NOT_BUILT})
            continue
        if int(attempts.get(task, 0)) >= int(policy["max_attempts"]):
            excluded.append({"task_id": task, "reason": REASON_RETRY_CAP})
            continue
        if task in terminal:
            excluded.append({"task_id": task, "reason": REASON_PREVIOUSLY_RECORDED})
            continue
        if task in recorded:
            excluded.append({"task_id": task, "reason": REASON_PREVIOUSLY_RECORDED})
            continue
        selected.append(task)
    if requested_set:
        for task in sorted(requested_set - set(registered)):
            excluded.append({"task_id": task, "reason": REASON_UNREGISTERED})
    selected = selected[: int(policy["max_tasks_per_batch"])]
    return selected, excluded


def _venv_python(workspace: Path, side: str) -> Path | None:
    candidates = (
        workspace / (".venv" if side == "base" else ".venv_gold") / "Scripts" / "python.exe",
        workspace / (".venv" if side == "base" else ".venv_gold") / "bin" / "python",
        workspace / ("repo" if side == "base" else "repo_gold") / ".venv" / "Scripts" / "python.exe",
        workspace / ("repo" if side == "base" else "repo_gold") / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _pytest_args(test_cmd: str) -> list[str]:
    text = str(test_cmd or "").strip()
    if not text:
        raise PrecheckError("declared test command is empty")
    if any(op in text for op in FORBIDDEN_SHELL):
        raise PrecheckError("declared test command contains a shell operator")
    parts = shlex.split(text, posix=False)
    if parts[:2] == ["uv", "run"]:
        parts = parts[2:]
    if parts[:1] == ["pytest"]:
        parts = parts[1:]
    elif parts[:3] == ["python", "-m", "pytest"]:
        parts = parts[3:]
    elif parts[:2] == ["py", "-m"] and parts[2:3] == ["pytest"]:
        parts = parts[3:]
    else:
        raise PrecheckError(f"declared command is not a pytest invocation: {text!r}")
    args: list[str] = []
    skip_next = False
    for token in parts:
        if skip_next:
            skip_next = False
            continue
        if token in RUN_ONLY_FLAGS or token.startswith("--tb="):
            if token in ("-r", "--maxfail", "--durations"):
                skip_next = True
            continue
        if token.startswith("--maxfail=") or token.startswith("--durations="):
            continue
        args.append(token)
    return args


def _parse_collect(stdout: str, stderr: str) -> dict[str, Any]:
    text = f"{stdout}\n{stderr}"
    nodes: list[str] = []
    errors: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("ERROR "):
            errors.append(line)
            continue
        if line.startswith(("E   ", "E\t")):
            errors.append(line)
            continue
        if "::" in line and not line.startswith(("=", "_", "ERROR")):
            node = line.split(" ")[0].strip()
            if "::" in node:
                nodes.append(node)
    return {"nodes": sorted(set(nodes)), "errors": errors}


def _target_collected(target: str, nodes: Sequence[str]) -> bool:
    for node in nodes:
        if node == target:
            return True
        if node.endswith("/" + target) or target.endswith("/" + node):
            return True
    return False


def _classify_side(
    *,
    side: str,
    returncode: int,
    stdout: str,
    stderr: str,
    targets: Sequence[str],
    changed_modules: Sequence[str],
) -> dict[str, Any]:
    parsed = _parse_collect(stdout, stderr)
    nodes = parsed["nodes"]
    errors = parsed["errors"]
    missing = [t for t in targets if not _target_collected(t, nodes)]
    base = {
        "side": side,
        "returncode": returncode,
        "collected_count": len(nodes),
        "collected_nodes": nodes,
        "collection_errors": len(errors),
        "targets_total": len(targets),
        "targets_missing": missing,
        "error_text": "\n".join(errors)[:4000],
    }
    if returncode == 0 and not errors and not missing:
        return {**base, "status": SIDE_PASS, "reason_codes": []}
    if errors:
        cause = classify_collection_cause(errors, changed_modules=changed_modules)
        base["collection_error_cause"] = cause
        if cause == "project" and side == "base":
            # The gold patch owns the module the base test imports; a base-side
            # collection failure is the expected SWE-bench shape, not a broken
            # environment.  This is the D1 rule.
            return {
                **base, "status": SIDE_PASS,
                "reason_codes": [], "note": "PROJECT_COLLECTION_EXPECTED_ON_BASE",
            }
        if cause == "project" and side == "gold":
            return {**base, "status": SIDE_FAIL,
                    "reason_codes": [REASON_GOLD_PROJECT_COLLECTION]}
        if cause == "environment":
            return {**base, "status": SIDE_FAIL,
                    "reason_codes": [REASON_ENVIRONMENT_COLLECTION]}
        return {**base, "status": SIDE_INCONCLUSIVE,
                "reason_codes": [REASON_UNKNOWN_COLLECTION]}
    if returncode == 5 or (not nodes and "no tests ran" in (stdout + stderr).lower()):
        return {**base, "status": SIDE_FAIL, "reason_codes": [REASON_NO_TESTS]}
    if missing:
        return {**base, "status": SIDE_INCONCLUSIVE,
                "reason_codes": [REASON_TARGET_NOT_COLLECTED]}
    return {**base, "status": SIDE_INCONCLUSIVE,
            "reason_codes": [REASON_COLLECT_UNPARSED]}


def _assert_isolated(
    out_dir: Path,
    *,
    repo_root: Path,
    ledger_paths: Sequence[Path],
    staging_root: Path | None,
) -> None:
    resolved = out_dir.resolve()
    forbidden: list[Path] = [Path(r"C:\Users\rober\F2_AUTHZ").resolve()]
    forbidden += [p.resolve().parent for p in ledger_paths]
    if staging_root is not None:
        forbidden.append(staging_root.resolve())
    from qwen_train.f2_authz_ledger import DEFAULT_LEDGER_ROOT

    forbidden.append(Path(DEFAULT_LEDGER_ROOT).resolve())
    for bad in forbidden:
        if resolved == bad or bad in resolved.parents or resolved in bad.parents:
            raise PrecheckError(
                f"output directory {resolved} overlaps protected state {bad}; "
                "choose an isolated folder"
            )
    if resolved == repo_root.resolve():
        raise PrecheckError("output directory may not be the repository root")
    for part in resolved.parts:
        if part.startswith("pilot_results"):
            raise PrecheckError(
                f"output directory {resolved} is a scored-results directory"
            )


def _default_runner(cmd: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: int):
    return subprocess.run(
        list(cmd), cwd=cwd, env=dict(env), capture_output=True, text=True,
        timeout=timeout,
    )


def run_precheck(
    *,
    repo_root: Path | str,
    build_manifest_path: Path | str,
    registration_path: Path | str,
    registration_id: str,
    registration_sha256: str,
    ledger_paths: Sequence[Path | str],
    out_dir: Path | str,
    inputs_dir: Path | str,
    staging_root: Path | str | None = None,
    tasks: Sequence[str] | None = None,
    policy: Mapping[str, Any] | None = None,
    expected_revision: str | None = None,
    expected_manifest_digest: str | None = None,
    expected_registration_sha256: str | None = None,
    expected_task_list_digest: str | None = None,
    expected_policy_digest: str | None = None,
    runner: Callable[..., Any] | None = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run the collection-only gate and return a deterministic result.

    ``runner`` is injectable for tests.  It receives ``(cmd, cwd, env, timeout)``
    and must return an object with ``returncode``, ``stdout`` and ``stderr``.
    """
    repo = Path(repo_root).resolve()
    manifest_file = Path(build_manifest_path)
    registration_file = Path(registration_path)
    ledgers = [Path(p) for p in ledger_paths]
    if not ledgers:
        raise PrecheckError("at least one ledger is required (both registered chains preferred)")
    out = Path(out_dir)
    _assert_isolated(out, repo_root=repo, ledger_paths=ledgers, staging_root=Path(staging_root) if staging_root else None)

    revision = _git_revision(repo)
    if expected_revision and revision != expected_revision:
        raise PrecheckError(
            f"revision is stale: repository HEAD is {revision}, expected {expected_revision}"
        )
    manifest_bytes = manifest_file.read_bytes()
    manifest_digest = _sha256_bytes(manifest_bytes)
    if expected_manifest_digest and manifest_digest != expected_manifest_digest:
        raise PrecheckError("manifest digest is stale")
    build_manifest = json.loads(manifest_bytes.decode("utf-8"))
    if not isinstance(build_manifest, Mapping):
        raise PrecheckError("build manifest is not a JSON object")
    registration_bytes = registration_file.read_bytes()
    registration_file_digest = _sha256_bytes(registration_bytes)
    registered = _registered_task_ids(registration_file)
    if expected_registration_sha256 and registration_sha256 != expected_registration_sha256:
        raise PrecheckError("registration digest is stale")

    resolved_policy = _policy(policy)
    policy_digest = _digest(resolved_policy)
    if expected_policy_digest and policy_digest != expected_policy_digest:
        raise PrecheckError("policy digest is stale")

    recorded, terminal, attempts = _ledger_state(ledgers)
    selected, excluded = select_candidates(
        registered=registered, build_manifest=build_manifest,
        recorded=recorded, terminal=terminal, attempts=attempts,
        policy=resolved_policy, requested=tasks,
    )
    task_list_digest = _digest({"task_ids": sorted(selected)})
    if expected_task_list_digest and task_list_digest != expected_task_list_digest:
        raise PrecheckError("candidate task list is stale")

    code_digest = _sha256_file(Path(__file__))
    run_id = _digest({
        "revision": revision,
        "manifest_digest": manifest_digest,
        "registration_sha256": registration_sha256,
        "task_list_digest": task_list_digest,
        "policy_digest": policy_digest,
        "precheck_version": PRECHECK_VERSION,
        "precheck_code_sha256": code_digest,
    })
    artifacts = out / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    execute = runner or _default_runner

    task_results: list[dict[str, Any]] = []
    for task in selected:
        entry = build_manifest.get(task) or {}
        workspace = Path(str(entry.get("workspace") or ""))
        record_path = _find_input_record(Path(inputs_dir), task)
        task_result: dict[str, Any] = {
            "task_id": task,
            "status": SIDE_INCONCLUSIVE,
            "reason_codes": [],
            "workspace": str(workspace) if str(workspace) else "",
            "sides": {},
            "parked": False,
        }
        if not str(workspace) or not workspace.is_dir():
            task_result.update({
                "status": SIDE_INCONCLUSIVE,
                "reason_codes": [REASON_WORKSPACE_MISSING],
                "parked": True,
            })
            task_results.append(task_result)
            continue
        if record_path is None:
            task_result.update({
                "status": SIDE_INCONCLUSIVE,
                "reason_codes": [REASON_INPUT_RECORD_MISSING],
                "parked": True,
            })
            task_results.append(task_result)
            continue
        record = json.loads(record_path.read_text(encoding="utf-8"))
        f2p = list(record.get("FAIL_TO_PASS") or record.get("fail_to_pass") or [])
        p2p = list(record.get("PASS_TO_PASS") or record.get("pass_to_pass") or [])
        targets = [str(t) for t in f2p + p2p]
        changed = changed_module_paths(record.get("patch") or "")
        test_cmd = record.get("test_cmd") or (record.get("test_cmds") or [""])[0]
        task_result["targets_total"] = len(targets)
        task_result["input_record"] = str(record_path)
        try:
            pytest_args = _pytest_args(str(test_cmd))
        except PrecheckError as exc:
            task_result.update({
                "status": SIDE_FAIL,
                "reason_codes": [REASON_TEST_CMD_UNSUPPORTED],
                "detail": str(exc),
                "parked": True,
            })
            task_results.append(task_result)
            continue

        side_statuses: list[str] = []
        for side in ("base", "gold"):
            python = _venv_python(workspace, side)
            if python is None:
                task_result["sides"][side] = {
                    "side": side, "status": SIDE_INCONCLUSIVE,
                    "reason_codes": [REASON_VENV_MISSING],
                }
                side_statuses.append(SIDE_INCONCLUSIVE)
                continue
            cwd = workspace / ("repo" if side == "base" else "repo_gold")
            env = dict(os.environ)
            env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            cmd = [
                str(python), "-m", "pytest", *pytest_args,
                "--collect-only", "-q", "-p", "no:cacheprovider", "--no-header",
            ]
            try:
                proc = execute(cmd, cwd, env, timeout_seconds)
                stdout = proc.stdout if isinstance(proc.stdout, str) else (proc.stdout or b"").decode(errors="replace")
                stderr = proc.stderr if isinstance(proc.stderr, str) else (proc.stderr or b"").decode(errors="replace")
                returncode = int(proc.returncode)
            except subprocess.TimeoutExpired:
                task_result["sides"][side] = {
                    "side": side, "status": SIDE_INCONCLUSIVE,
                    "reason_codes": [REASON_COLLECT_TIMEOUT],
                }
                side_statuses.append(SIDE_INCONCLUSIVE)
                continue
            except Exception as exc:  # noqa: BLE001
                task_result["sides"][side] = {
                    "side": side, "status": SIDE_INCONCLUSIVE,
                    "reason_codes": [REASON_COLLECT_ERROR], "detail": str(exc),
                }
                side_statuses.append(SIDE_INCONCLUSIVE)
                continue
            (artifacts / f"{task}.{side}.stdout.txt").write_text(stdout, encoding="utf-8")
            (artifacts / f"{task}.{side}.stderr.txt").write_text(stderr, encoding="utf-8")
            side_result = _classify_side(
                side=side, returncode=returncode, stdout=stdout, stderr=stderr,
                targets=targets, changed_modules=changed,
            )
            side_result["command"] = cmd
            side_result["venv_python"] = str(python)
            task_result["sides"][side] = side_result
            side_statuses.append(side_result["status"])

        if SIDE_FAIL in side_statuses:
            task_result["status"] = SIDE_FAIL
        elif all(s == SIDE_PASS for s in side_statuses) and len(side_statuses) == 2:
            task_result["status"] = SIDE_PASS
        else:
            task_result["status"] = SIDE_INCONCLUSIVE
        codes: list[str] = []
        for side in ("base", "gold"):
            codes.extend((task_result["sides"].get(side) or {}).get("reason_codes") or [])
        task_result["reason_codes"] = sorted(set(codes))
        task_result["parked"] = task_result["status"] != SIDE_PASS
        task_results.append(task_result)

    failed = [t for t in task_results if t["status"] == SIDE_FAIL]
    inconclusive = [t for t in task_results if t["status"] == SIDE_INCONCLUSIVE]
    passed = [t for t in task_results if t["status"] == SIDE_PASS]
    if not selected:
        verdict = SIDE_INCONCLUSIVE
    elif failed:
        verdict = SIDE_FAIL
    elif inconclusive:
        verdict = SIDE_INCONCLUSIVE
    else:
        verdict = SIDE_PASS

    result: dict[str, Any] = {
        "schema": PRECHECK_SCHEMA,
        "version": PRECHECK_VERSION,
        "verdict": verdict,
        "bindings": {
            "repository_root": str(repo),
            "revision": revision,
            "build_manifest_path": str(manifest_file.resolve()),
            "manifest_digest": manifest_digest,
            "registration_path": str(registration_file.resolve()),
            "registration_file_sha256": registration_file_digest,
            "registration_id": registration_id,
            "registration_sha256": registration_sha256,
            "task_list_digest": task_list_digest,
            "policy_digest": policy_digest,
            "precheck_code_sha256": code_digest,
            "run_id": run_id,
            "out_dir": str(out.resolve()),
        },
        "policy": resolved_policy,
        "counts": {
            "registered": len(registered),
            "recorded": len(recorded),
            "built": sum(
                1 for v in build_manifest.values()
                if isinstance(v, Mapping) and v.get("status") == "built"
            ),
            "registered_unbuilt": len(set(registered) - {
                str(k) for k, v in build_manifest.items()
                if isinstance(v, Mapping) and v.get("status") == "built"
            }),
            "candidates": len(selected),
            "passed": len(passed),
            "failed": len(failed),
            "inconclusive": len(inconclusive),
            "parked": sum(1 for t in task_results if t["parked"]) + len(excluded),
            "excluded": len(excluded),
        },
        "tasks": sorted(task_results, key=lambda t: t["task_id"]),
        "excluded": sorted(excluded, key=lambda e: (e["task_id"], e["reason"])),
        "notes": [
            "collection-only compatibility is NOT scientific admission",
            "no grant was consumed; no ledger row was written; no credit was awarded",
            "PASS requires both sides to collect every declared target (base-side "
            "PROJECT-owned collection errors are the expected SWE-bench shape)",
        ],
    }
    result["result_digest"] = _digest(result)
    output = out / "precheck.json"
    with open(output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return result


def _find_input_record(inputs_dir: Path, task_id: str) -> Path | None:
    """Locate the task's full input record under the caller-supplied inputs root."""
    for root in (inputs_dir, inputs_dir / "full"):
        candidate = root / f"{task_id}.json"
        if candidate.is_file():
            return candidate
    return None


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m qwen_train.f2_s8_precheck")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--build-manifest", required=True)
    parser.add_argument("--registration", required=True)
    parser.add_argument("--registration-id", required=True)
    parser.add_argument("--registration-sha256", required=True)
    parser.add_argument("--ledger", action="append", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--inputs", required=True,
                        help="directory holding full task input records")
    parser.add_argument("--staging-root", default="")
    parser.add_argument("--task", action="append", default=None)
    parser.add_argument("--policy", default="")
    parser.add_argument("--expected-revision", default="")
    parser.add_argument("--expected-manifest-digest", default="")
    parser.add_argument("--expected-registration-sha256", default="")
    parser.add_argument("--expected-task-list-digest", default="")
    parser.add_argument("--expected-policy-digest", default="")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(list(argv) if argv is not None else None)
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8")) if args.policy else None
    result = run_precheck(
        repo_root=args.repo_root,
        build_manifest_path=args.build_manifest,
        registration_path=args.registration,
        registration_id=args.registration_id,
        registration_sha256=args.registration_sha256,
        ledger_paths=args.ledger,
        out_dir=args.out_dir,
        inputs_dir=args.inputs,
        staging_root=args.staging_root or None,
        tasks=args.task,
        policy=policy,
        expected_revision=args.expected_revision or None,
        expected_manifest_digest=args.expected_manifest_digest or None,
        expected_registration_sha256=args.expected_registration_sha256 or None,
        expected_task_list_digest=args.expected_task_list_digest or None,
        expected_policy_digest=args.expected_policy_digest or None,
        timeout_seconds=args.timeout,
    )
    print(json.dumps({
        "verdict": result["verdict"], "counts": result["counts"],
        "run_id": result["bindings"]["run_id"],
        "out": str(Path(args.out_dir) / "precheck.json"),
    }, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
