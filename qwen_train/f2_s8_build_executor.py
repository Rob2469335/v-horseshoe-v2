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
  refusal must stop the run, not become a task result.
* The returned workspace must exist and the result must name the requested task,
  or the adapter fails closed (``BuilderContractError``).

Not wired into ``--live``
-------------------------
``python -m qwen_train.f2_s8_probe_driver --live`` remains hard-refused by the
driver's fail-closed policy. :func:`make_probe_live_builder` is the composition
point a future, owner-approved live path would use; it is intentionally not
called by the driver today.
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any, Callable, Iterable, Mapping

from qwen_train.f2_s8_probe_driver import (
    BuildCell,
    BuilderContractError,
    LiveBuilder,
)

#: Reason codes that may be treated as a deterministic *incompatibility* rather
#: than an infrastructure failure. Empty by default: mapping any code to
#: OBSERVED_INCOMPATIBLE is an owner policy decision, not an implementation
#: default.
DEFAULT_DETERMINISTIC_INCOMPATIBILITY_CODES: frozenset[str] = frozenset()


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
        build_fn: Callable[..., Mapping[str, Any]] | None = None,
        classify_fn: Callable[[BaseException], str] | None = None,
        resolve_url_fn: Callable[[str, pathlib.Path], str] | None = None,
        declared_minor_fn: Callable[[str, pathlib.Path], str | None] | None = None,
        deterministic_incompatibility_codes: Iterable[str] = DEFAULT_DETERMINISTIC_INCOMPATIBILITY_CODES,
    ) -> None:
        self._authorized = authorized_run
        self._inputs = pathlib.Path(inputs)
        self._patches = pathlib.Path(patches)
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
            declared_minor_fn = declared_minor_fn or pb._declare_from_input
        self._build = build_fn
        self._classify = classify_fn
        self._resolve_url = resolve_url_fn
        self._declared_minor = declared_minor_fn
        self._deterministic = frozenset(deterministic_incompatibility_codes)

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
            )
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
                # An authorization refusal must stop the run, not become a cell.
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
        workspace = result.get("workspace")
        if not workspace or not pathlib.Path(str(workspace)).is_dir():
            raise BuilderContractError(
                f"build reported success without an existing workspace for {task_id!r}"
            )
        elapsed = result.get("elapsed_s")
        return BuildCell(
            task_id,
            "OBSERVED_COMPATIBLE",
            workspace=str(workspace),
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
        authorized_run, inputs=inputs, patches=patches, **executor_kwargs
    )
    return LiveBuilder(executor.build_one, workspace_root=workspace_root)
