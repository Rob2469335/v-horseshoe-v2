"""Per-task frozen behavioral endpoint for Experiment J F2 (F0 section 5).

Why this module exists
----------------------
F0 froze the primary endpoint as

    first edit-type filesystem action within the first ``k`` decision steps
    whose ``path`` belongs to a predefined frozen ``relevant_file_set``

and F1-OP-003 froze ``relevant_file_set = {"swarm_os/lib/paths.py"}`` for the
*single* F1 pilot task. That left two defects for a multi-task F2 population:

1. ``f1_infra.find_qualifying_first_edit`` read the module-level
   ``F1_RELEVANT_FILE_SET`` constant, so the detector could only ever measure
   the F1 task. Using it for any other task silently yields ``None`` -- i.e. a
   guaranteed "no qualifying edit", which would be scored as a real observation.
2. Nothing tied the detector to a *frozen, hashed* per-task set, so the set
   could in principle be chosen after seeing T/X outcomes.

This module makes the endpoint an explicit, hash-bound, immutable specification
per task, and makes the detector read that specification instead of a constant.

Governance properties
---------------------
* **Fail closed.** A missing, unsafe, or mismatched set raises
  :class:`EndpointError`. It never falls back to a default set.
* **Treatment-independent.** The specification is derived from the task
  (declared FAIL_TO_PASS provenance), never from an observed T or X trajectory.
  :func:`assert_treatment_independent` makes that checkable.
* **Frozen by hash.** The set is serialized, sorted and SHA-256 bound, and the
  hash is cross-checked against the readiness declaration that is itself inside
  the verified manifest.
* **Horizon is explicit.** ``k`` travels with the specification rather than being
  a module constant, so a future horizon change cannot silently alter a frozen
  experiment's endpoint.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from runtime_v2.services.task_readiness import (
    canonical_relevant_file_set,
    compute_relevant_file_set_hash,
)

__all__ = [
    "EndpointError",
    "QUALIFYING_OPERATIONS",
    "DEFAULT_HORIZON_STEPS",
    "FrozenEndpoint",
    "freeze_endpoint",
    "qualifying_first_edit",
    "assert_treatment_independent",
]

#: The edit-type filesystem operations that can satisfy the endpoint (F0 section 5
#: / F1-OP-004-CLARIFICATION). An ACTION qualifies; acceptance by the filesystem
#: tool, actual file mutation and repair correctness are NOT required.
QUALIFYING_OPERATIONS = frozenset({"write", "patch", "edit", "create"})

#: F1-OP-004a froze a 12-ATIF-decision-step horizon; F0 section 5 clamps the
#: admissible k range to [8, 12].
DEFAULT_HORIZON_STEPS = 12

MIN_HORIZON_STEPS = 8
MAX_HORIZON_STEPS = 12

#: Provenance strings that are NOT acceptable as a derivation source for a
#: confirmatory endpoint. Recorded so an operator cannot pick one of these to
#: make a task "admissible".
DISALLOWED_DERIVATION_SOURCES = frozenset(
    {
        "observed_t_trajectory",
        "observed_x_trajectory",
        "t_outcome",
        "x_outcome",
        "post_hoc",
        "operator_preference",
        "manual_after_outcomes",
    }
)


class EndpointError(ValueError):
    """Raised when a task's endpoint specification is missing or inconsistent."""


def _normalize_target(raw: object) -> str:
    return str(raw or "").replace("\\", "/").strip()


def _matches(target: str, rel_path: str) -> bool:
    """True iff an absolute-or-relative tool target denotes ``rel_path``.

    Tolerant of an absolute workspace path, a repo-relative path, and Windows
    separators, because the same logical file legitimately appears in all three
    forms across the F1/F2 tool surfaces. Anchored on a path SEGMENT boundary so
    ``src/paths.py`` cannot match ``other/src/paths.py.bak`` by accident.
    """
    if not target:
        return False
    norm_target = target.replace("\\", "/")
    norm_rel = rel_path.replace("\\", "/").lstrip("./")
    if norm_target == norm_rel:
        return True
    if norm_target.endswith("/" + norm_rel):
        return True
    # Windows drive-letter / UNC absolute paths.
    pure = PurePosixPath(norm_target)
    if pure.name == PurePosixPath(norm_rel).name and pure.suffix == PurePosixPath(
        norm_rel
    ).suffix:
        parts = norm_rel.split("/")
        if len(parts) == 1:  # bare filename: match only the final segment
            return True
        return norm_target.endswith("/".join(parts))
    return False


@dataclass(frozen=True)
class FrozenEndpoint:
    """An immutable, hash-bound endpoint specification for ONE task."""

    task_id: str
    relevant_file_set: tuple[str, ...]
    relevant_file_set_hash: str
    horizon_steps: int = DEFAULT_HORIZON_STEPS
    derivation_source: str = "fail_to_pass_provenance"
    derivation_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.task_id or "").strip():
            raise EndpointError("FrozenEndpoint requires a task_id")
        canonical = canonical_relevant_file_set(self.relevant_file_set)
        if tuple(canonical) != tuple(self.relevant_file_set):
            raise EndpointError(
                "relevant_file_set must be stored canonical (sorted, normalized): "
                f"{self.relevant_file_set!r} != {canonical!r}"
            )
        recomputed = compute_relevant_file_set_hash(canonical)
        if self.relevant_file_set_hash != recomputed:
            raise EndpointError(
                "relevant_file_set_hash does not match the set: "
                f"{self.relevant_file_set_hash!r} != {recomputed!r}"
            )
        if not isinstance(self.horizon_steps, int) or not (
            MIN_HORIZON_STEPS <= self.horizon_steps <= MAX_HORIZON_STEPS
        ):
            raise EndpointError(
                f"horizon_steps must be an int in [{MIN_HORIZON_STEPS},"
                f"{MAX_HORIZON_STEPS}] (F0 section 5 admissible k range), got "
                f"{self.horizon_steps!r}"
            )
        if self.derivation_source in DISALLOWED_DERIVATION_SOURCES:
            raise EndpointError(
                "endpoint derivation_source is not admissible for a confirmatory "
                f"endpoint: {self.derivation_source!r}. The relevant file set must "
                "be derived from the task's declared FAIL_TO_PASS provenance, "
                "never from an observed arm or its outcome."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "relevant_file_set": list(self.relevant_file_set),
            "relevant_file_set_hash": self.relevant_file_set_hash,
            "horizon_steps": self.horizon_steps,
            "derivation_source": self.derivation_source,
            "derivation_evidence": list(self.derivation_evidence),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FrozenEndpoint":
        if not isinstance(payload, Mapping) or not payload:
            raise EndpointError("endpoint payload is empty or not a mapping")
        return cls(
            task_id=str(payload.get("task_id") or ""),
            relevant_file_set=tuple(payload.get("relevant_file_set") or ()),
            relevant_file_set_hash=str(payload.get("relevant_file_set_hash") or ""),
            horizon_steps=int(payload.get("horizon_steps") or DEFAULT_HORIZON_STEPS),
            derivation_source=str(
                payload.get("derivation_source") or "fail_to_pass_provenance"
            ),
            derivation_evidence=tuple(payload.get("derivation_evidence") or ()),
        )


def freeze_endpoint(
    *,
    task_id: str,
    relevant_file_set: Iterable[object],
    horizon_steps: int = DEFAULT_HORIZON_STEPS,
    derivation_source: str = "fail_to_pass_provenance",
    derivation_evidence: Iterable[object] = (),
) -> FrozenEndpoint:
    """Canonicalize, hash and freeze one task's endpoint. Fail closed."""
    if derivation_source in DISALLOWED_DERIVATION_SOURCES:
        raise EndpointError(
            f"derivation_source {derivation_source!r} is not admissible"
        )
    try:
        canonical = canonical_relevant_file_set(relevant_file_set)
    except Exception as exc:  # noqa: BLE001 - normalize to the module's error type
        raise EndpointError(f"invalid relevant_file_set: {exc}") from exc
    return FrozenEndpoint(
        task_id=task_id,
        relevant_file_set=canonical,
        relevant_file_set_hash=compute_relevant_file_set_hash(canonical),
        horizon_steps=int(horizon_steps),
        derivation_source=derivation_source,
        derivation_evidence=tuple(str(e) for e in derivation_evidence),
    )


def endpoint_payload_hash(spec: FrozenEndpoint) -> str:
    """Stable SHA-256 over the whole endpoint specification.

    Hashing the specification (not just the file set) means a horizon or
    derivation-source change is also detectable, so a frozen experiment cannot
    be re-scored under a quietly altered endpoint.
    """
    import hashlib

    payload = json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def qualifying_first_edit(
    tool_calls: Iterable[Mapping[str, Any]],
    spec: FrozenEndpoint,
) -> dict[str, Any] | None:
    """Return the first qualifying endpoint for ``spec``, or ``None``.

    The detection rule is F0 section 5 / F1-OP-004-CLARIFICATION, applied to the
    specification's own file set and horizon:

      * ``function_name == "filesystem"``
      * ``operation in {write, patch, edit, create}``
      * target path resolves to a member of ``spec.relevant_file_set``
      * ATIF ``step_id <= spec.horizon_steps``

    An action QUALIFIES on being attempted. Filesystem acceptance, actual file
    mutation, patch success and repair correctness are deliberately NOT required
    (F1-OP-004-CLARIFICATION), which is what makes the endpoint objective and
    free of evaluator judgement.

    Returns a dict (not a bare int) so downstream analysis records WHICH file and
    step produced the endpoint without re-parsing the trajectory.
    """
    dict_tcs = [tc for tc in tool_calls if isinstance(tc, Mapping)]
    if not dict_tcs:
        return None

    def _step(tc: Mapping[str, Any]) -> int:
        extra = tc.get("extra") or {}
        raw = extra.get("step_id")
        if raw is None:
            raw = extra.get("turn", 0)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    for tc in sorted(dict_tcs, key=_step):
        if str(tc.get("function_name") or "") != "filesystem":
            continue
        args = tc.get("arguments") or {}
        operation = str(args.get("operation") or "")
        if operation not in QUALIFYING_OPERATIONS:
            continue
        target = _normalize_target(args.get("path") or args.get("file_path") or "")
        for rel_path in spec.relevant_file_set:
            if _matches(target, rel_path):
                step_id = _step(tc)
                if step_id > spec.horizon_steps:
                    return None  # past the horizon: no endpoint, and no later one
                return {
                    "step_id": step_id,
                    "operation": operation,
                    "target": target,
                    "relevant_file": rel_path,
                    "horizon_steps": spec.horizon_steps,
                }
    return None


def assert_treatment_independent(spec: FrozenEndpoint) -> None:
    """Fail closed if the endpoint could have been derived from an outcome.

    A ``relevant_file_set`` chosen after seeing whether T or X edited quickly
    would make the primary endpoint treatment-dependent by construction. This is
    the machine-checkable form of that prohibition.
    """
    if spec.derivation_source in DISALLOWED_DERIVATION_SOURCES:
        raise EndpointError(
            f"endpoint for {spec.task_id!r} declares a treatment-derived source: "
            f"{spec.derivation_source!r}"
        )
    if not spec.derivation_evidence:
        raise EndpointError(
            f"endpoint for {spec.task_id!r} records no derivation evidence; a "
            "confirmatory endpoint must carry the provenance that produced it"
        )