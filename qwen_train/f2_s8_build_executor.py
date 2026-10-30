"""Adapter: existing per-task pilot build -> build-probe ``LiveBuilder`` contract.

Why this exists
---------------
``qwen_train.f2_s8_pilot_build.build(task_id, url, ..., authorized_run=...)``
IS the repository's approved per-task build entry point: it accepts a probe
authorization (``S8ProbeAuthorizedRun`` / operation ``build_probe``), enforces
the unprivileged worker identity itself, returns a result mapping on success,
and raises on failure with a deterministic reason code from
``classify_build_failure``. This module is a thin, testable adapter that maps
that contract onto :class:`qwen_train.f2_s8_probe_driver.BuildCell`; it does not
introduce a second build system.

Honesty rules
-------------
* A raised build error becomes a ``BUILD_FAILURE`` cell carrying the
  deterministic ``reason_code``. It is never reported as success.
* An ``OBSERVED_INCOMPATIBLE`` cell is produced ONLY for reason codes the owner
  has explicitly declared deterministic via
  ``deterministic_incompatibility_codes``. The default set is **empty**, so
  nothing is labelled "incompatible" without an owner decision.
* ``BUILD_NOT_AUTHORIZED`` is re-raised (never swallowed): an authorization
  refusal must stop the run, not become a task result. Any
  :class:`qwen_train.f2_authorization.AuthorizationError` (which includes
  ``V2GateError`` and ``BudgetError`` — expiration, revocation, invalid
  authorization, artifact drift, forbidden ledger mutation) is likewise
  re-raised immediately and aborts the run.
* The returned workspace must exist, name the requested task, and be the
  task-specific directory (``workspace_root / task_id``) *strictly inside* the
  designated workspace root; symlink/relative escapes and foreign-task
  workspaces are rejected. Anything else fails closed
  (``BuilderContractError``).

Not wired into ``--live``
-------------------------
``python -m qwen_train.f2_s8_probe_driver --live`` IS wired: its ``main``
constructs this builder via :func:`make_probe_live_builder` once ``--inputs`` and
``--patches`` are present, the signed grant verifies, and the identity guard
passes.  Before reserving budget it refuses with ``PROBE_PREREQUISITES_MISSING``
when either directory is absent; construction failures are reported the same
way and consume nothing.

Compatibility means collectable
-------------------------------
A successful ``pilot_build.build`` proves the workspace and environment were
created -- not that the task's declared tests exist in it.  After a successful
build, the executor runs ``pytest --collect-only`` in the built task environment
and marks the cell ``OBSERVED_COMPATIBLE`` only when EVERY declared
FAIL_TO_PASS node is collected.  Otherwise the cell is a ``BUILD_FAILURE``
carrying ``F2P_NOT_DECLARED`` / ``VENV_MISSING`` / ``TEST_CMD_UNSUPPORTED`` /
``F2P_NOT_COLLECTED`` / ``COLLECT_TIMEOUT`` / ``COLLECT_FAILED``.  Collection is
still not evidence that the tests fail at base or pass with the gold patch; it
only removes "built but its declared tests do not even exist here" from the
compatible set.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Any, Callable, Iterable, Mapping

from qwen_train.f2_authorization import AuthorizationError
from qwen_train.f2_s8_probe_driver import (
    BuildCell,
    BuilderContractError,
    LiveBuilder,
    validate_task_workspace,
)

#: Reason codes that may be treated as a deterministic *incompatibility* rather
#: than an infrastructure failure. Empty by default: mapping any code to
#: OBSERVED_INCOMPATIBLE is an owner policy decision, not an implementation
#: default.
DEFAULT_DETERMINISTIC_INCOMPATIBILITY_CODES: frozenset[str] = frozenset()


def declared_contract(
    inputs: pathlib.Path | str, task_id: str,
) -> tuple[list[str], str]:
    """Read the task's declared ``FAIL_TO_PASS`` nodes and first test command.

    The record is the same acquisition artifact ``pilot_build.build`` consumes.
    A missing/unreadable record raises: the caller already refuses that case.
    """
    path = pathlib.Path(inputs) / f"{task_id}.json"
    if not path.is_file():
        from qwen_train.f2_s8_precheck import _find_input_record

        found = _find_input_record(pathlib.Path(inputs), task_id)
        if found is None:
            raise FileNotFoundError(f"task input record missing for {task_id}")
        path = found
    record = json.loads(path.read_text("utf-8"))
    f2p = [str(x) for x in (record.get("FAIL_TO_PASS") or record.get("fail_to_pass") or [])]
    cmd = str(record.get("test_cmd") or "")
    if not cmd:
        for entry in record.get("test_cmds") or []:
            if isinstance(entry, str) and entry.strip():
                cmd = entry
                break
            if isinstance(entry, Mapping):
                cmd = str(entry.get("cmd") or entry.get("command") or "")
                if cmd:
                    break
    return f2p, cmd


def check_declared_nodes_collect(
    workspace: pathlib.Path | str,
    fail_to_pass: Iterable[str],
    test_cmd: str = "",
    *,
    python: pathlib.Path | str | None = None,
    timeout: int = 900,
) -> tuple[bool, str, str]:
    """Collect-only check in the built task environment.

    Returns ``(ok, reason_code, detail)``.  ``ok`` is True only when every
    declared FAIL_TO_PASS node is present in ``pytest --collect-only`` output.
    Ordering/canonicalisation is delegated to the SAME helpers the Stage-1
    pre-check uses, so a node accepted here is a node the scorer can resolve.
    """
    from qwen_train.f2_s8_precheck import (
        _canonical_nodes,
        _parse_collect,
        _pytest_args,
        _target_collected,
        _venv_python,
    )

    targets = [str(t) for t in fail_to_pass]
    if not targets:
        return (
            False,
            "F2P_NOT_DECLARED",
            "no declared FAIL_TO_PASS nodes; compatibility cannot be observed",
        )
    ws = pathlib.Path(workspace)
    interpreter = pathlib.Path(python) if python is not None else _venv_python(ws, "base")
    if interpreter is None or not interpreter.is_file():
        return False, "VENV_MISSING", f"task venv interpreter not found under {ws}"
    try:
        pytest_args = _pytest_args(test_cmd) if test_cmd else []
    except Exception as exc:  # noqa: BLE001 - declared command is untrusted input
        return False, "TEST_CMD_UNSUPPORTED", str(exc)
    project_root = ws / "repo"
    if not project_root.is_dir():
        return False, "WORKSPACE_INVALID", f"task repo directory missing: {project_root}"

    env = dict(os.environ)
    env["PATH"] = str(interpreter.parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    qwen_train_dir = str(pathlib.Path(__file__).resolve().parent)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{qwen_train_dir}{os.pathsep}{existing}" if existing else qwen_train_dir
    )
    cmd = [
        str(interpreter), "-m", "pytest", *pytest_args,
        "--collect-only", "-q", "-p", "no:cacheprovider",
        "-p", "_pytest_no_network", "--no-header",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(project_root), capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "COLLECT_TIMEOUT", f"collection exceeded {timeout}s"
    except OSError as exc:
        return False, "COLLECT_FAILED", f"collection could not start: {exc}"

    nodes = _parse_collect(proc.stdout or "", proc.stderr or "")["nodes"]
    canonical = _canonical_nodes(nodes)
    missing = [t for t in targets if not _target_collected(t, nodes, canonical=canonical)]
    if missing:
        return (
            False,
            "F2P_NOT_COLLECTED",
            f"{len(missing)} of {len(targets)} declared FAIL_TO_PASS node(s) not "
            f"collected: {missing[:5]}",
        )
    if proc.returncode != 0:
        return (
            False,
            "COLLECT_FAILED",
            f"collection exited {proc.returncode} after listing every target",
        )
    return True, "", f"all {len(targets)} declared FAIL_TO_PASS nodes collected"


def _load_pilot_build():
    from qwen_train import f2_s8_pilot_build as pb

    return pb


class HostBuildExecutor:
    """Turn ``pilot_build.build`` into a per-task ``build_one`` callable.

    Dependencies are injectable so tests can exercise the mapping without a real
    build; when omitted they resolve to the real pilot-build functions.
    """

    def __init__(
        self,
        authorized_run: Any,
        *,
        inputs: pathlib.Path | str,
        patches: pathlib.Path | str,
        workspace_root: pathlib.Path | str | None = None,
        build_fn: Callable[..., Mapping[str, Any]] | None = None,
        classify_fn: Callable[[BaseException], str] | None = None,
        resolve_url_fn: Callable[[str, pathlib.Path], str] | None = None,
        declared_minor_fn: Callable[[str, pathlib.Path], str | None] | None = None,
        deterministic_incompatibility_codes: Iterable[str] = DEFAULT_DETERMINISTIC_INCOMPATIBILITY_CODES,
        collect_fn: Callable[[Any, Any, str], tuple[bool, str, str]] | None = None,
    ) -> None:
        self._authorized = authorized_run
        self._inputs = pathlib.Path(inputs)
        self._patches = pathlib.Path(patches)
        self._workspace_root = (
            pathlib.Path(workspace_root) if workspace_root is not None else None
        )
        if (
            build_fn is None
            or classify_fn is None
            or resolve_url_fn is None
            or declared_minor_fn is None
        ):
            pb = _load_pilot_build()
            build_fn = build_fn or pb.build
            classify_fn = classify_fn or pb.classify_build_failure
            resolve_url_fn = resolve_url_fn or pb.resolve_repo_url
            declared_minor_fn = declared_minor_fn or pb._declared_minor_for
        self._build = build_fn
        self._classify = classify_fn
        self._resolve_url = resolve_url_fn
        self._declared_minor = declared_minor_fn
        self._deterministic = frozenset(deterministic_incompatibility_codes)
        self._collect = collect_fn or check_declared_nodes_collect

    def _workspace_root_value(self) -> pathlib.Path:
        if self._workspace_root is None:
            # The approved build writes to ``f2_s8_pilot_build.ROOT / task_id``.
            self._workspace_root = pathlib.Path(_load_pilot_build().ROOT)
        return self._workspace_root

    def _validate_workspace(self, task_id: str, workspace: object) -> str:
        """Delegate to the single shared containment guard."""
        return validate_task_workspace(
            task_id, workspace, self._workspace_root_value()
        )

    def build_one(self, task_id: str) -> BuildCell:
        selected = tuple(getattr(self._authorized, "selected_task_ids", ()) or ())
        if selected and task_id not in set(selected):
            raise BuilderContractError(
                f"task {task_id!r} is not in the authorized probe selection"
            )
        if not (self._inputs / f"{task_id}.json").is_file():
            # No acquisition record: a pure prerequisite failure, never a success
            # and never a task incompatibility.
            return BuildCell(
                task_id,
                "BUILD_FAILURE",
                failure="task input record missing (acquisition not performed)",
                reason_code="TASK_INPUT_MISSING",
            )
        try:
            url = self._resolve_url(task_id, self._inputs)
            if not url:
                return BuildCell(
                    task_id,
                    "BUILD_FAILURE",
                    failure="no resolvable repository url for task",
                    reason_code="SOURCE_ACQUISITION_FAILURE",
                )
            declared = self._declared_minor(task_id, self._inputs)
            result = self._build(
                task_id,
                url,
                inputs=self._inputs,
                patches=self._patches,
                declared_python_minor=declared,
                authorized_run=self._authorized,
                root=str(self._workspace_root_value()),
            )
        except AuthorizationError:
            # Expiration, revocation, invalid authorization, artifact drift,
            # forbidden ledger mutation, budget failure: abort the run — never a
            # per-task result. This is type-based (V2GateError/BudgetError are
            # AuthorizationError), not a substring search.
            raise
        except subprocess.TimeoutExpired:
            return BuildCell(
                task_id,
                "BUILD_FAILURE",
                failure="build timed out",
                reason_code="BUILD_TIMEOUT",
            )
        except Exception as exc:  # noqa: BLE001 - map to an explicit reason code
            code = self._classify(exc)
            if code == "BUILD_NOT_AUTHORIZED":
                # Legacy string refusal must also stop the run, not become a cell.
                raise
            if code in self._deterministic:
                return BuildCell(
                    task_id, "OBSERVED_INCOMPATIBLE", failure="", reason_code=code
                )
            return BuildCell(
                task_id,
                "BUILD_FAILURE",
                failure=f"{code}: {str(exc)[:300]}",
                reason_code=code,
            )

        # Validate the result BEFORE calling it success.
        if not isinstance(result, Mapping):
            raise BuilderContractError(
                f"build returned {type(result).__name__}, expected a mapping"
            )
        if str(result.get("task_id")) != task_id:
            raise BuilderContractError(
                f"build returned task_id {result.get('task_id')!r} for {task_id!r}"
            )
        workspace = self._validate_workspace(task_id, result.get("workspace"))
        # A built workspace is not a compatible task: the declared tests must
        # exist in it.  Collect-only runs in the built environment before any
        # compatibility claim is made.
        try:
            f2p, test_cmd = declared_contract(self._inputs, task_id)
        except Exception as exc:  # noqa: BLE001 - untrusted input record
            return BuildCell(
                task_id,
                "BUILD_FAILURE",
                failure=f"task input record unreadable: {str(exc)[:300]}",
                reason_code="TASK_INPUT_INVALID",
            )
        collect_ok, collect_code, collect_detail = self._collect(workspace, f2p, test_cmd)
        if not collect_ok:
            return BuildCell(
                task_id,
                "BUILD_FAILURE",
                failure=collect_detail,
                reason_code=collect_code,
            )
        elapsed = result.get("elapsed_s")
        return BuildCell(
            task_id,
            "OBSERVED_COMPATIBLE",
            workspace=workspace,
            duration_seconds=float(elapsed) if isinstance(elapsed, (int, float)) else None,
        )


def make_probe_live_builder(
    authorized_run: Any,
    *,
    inputs: pathlib.Path | str,
    patches: pathlib.Path | str,
    workspace_root: pathlib.Path | str,
    **executor_kwargs: Any,
) -> LiveBuilder:
    """Compose the host build executor into a :class:`LiveBuilder`.

    This is the wiring point for a future, owner-approved live probe. It is NOT
    called by ``f2_s8_probe_driver.main``; the driver's ``--live`` refusal stays
    in force until the owner policy decision and operational prerequisites are
    satisfied.
    """
    executor = HostBuildExecutor(
        authorized_run,
        inputs=inputs,
        patches=patches,
        workspace_root=workspace_root,
        **executor_kwargs,
    )
    return LiveBuilder(executor.build_one, workspace_root=workspace_root)
