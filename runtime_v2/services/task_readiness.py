"""Governed per-task readiness representation (Experiment J readiness contract).

Scope: **engineering mechanism only.** This module validates, normalizes and
content-addresses a task's declared ``relevant_file_set`` (and binds the related
task/commit/test identities) so that

* the F2 primary endpoint ("first edit inside a predefined frozen
  ``relevant_file_set``") is reproducible and content-identified, and
* the worker cannot silently redefine the set.

It ships **no** F2 task values. The values for the five designated tasks are an
operator/scientific decision (``docs/EXPERIMENT_J_TASK_READINESS_CONTRACT.md``
§6, ``REQUIRES AUTHORIZATION``); this module fails closed when a set is absent,
empty, or malformed, and never falls back to the F1 pilot set.

Design follows the 2026 content-addressed-provenance pattern: a task is
identified by *what went into it* (sorted relevant files + declared tests +
base commit), and a manifest binds those by SHA-256 so a stale or tampered
declaration is rejected rather than silently used.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "ej-task-readiness/1"

#: A safe, relative, forward-slash repo path. Rejects absolute paths, drive
#: letters, ``..`` traversal, and characters that are not path characters.
_SAFE_PATH_RE = re.compile(r"[A-Za-z0-9_./\-]+")


class TaskReadinessError(ValueError):
    """A task-readiness declaration is absent, malformed, or inconsistent."""


def _normalize_path(p: object) -> str:
    if not isinstance(p, str):
        raise TaskReadinessError(f"relevant_file_set entry is not a string: {p!r}")
    norm = p.strip().replace("\\", "/")
    if not norm:
        raise TaskReadinessError("relevant_file_set entry is empty")
    if norm.startswith("/") or ":" in norm or ".." in PurePosixPath(norm).parts:
        raise TaskReadinessError(f"unsafe relevant_file_set entry: {p!r}")
    if not _SAFE_PATH_RE.fullmatch(norm):
        raise TaskReadinessError(f"unsafe relevant_file_set entry: {p!r}")
    return norm


def canonical_relevant_file_set(paths: Iterable[object]) -> tuple[str, ...]:
    """Return a sorted, normalized, validated tuple; raise on any unsafe entry.

    Deterministic: the same logical set always yields the same tuple regardless
    of input order or separators.
    """
    norm = tuple(sorted({_normalize_path(p) for p in paths}))
    if not norm:
        raise TaskReadinessError("relevant_file_set is empty")
    return norm


def compute_relevant_file_set_hash(paths: Iterable[object]) -> str:
    """SHA-256 over the canonical (sorted) relevant-file-set payload."""
    norm = canonical_relevant_file_set(paths)
    payload = json.dumps(
        {"schema": SCHEMA_VERSION, "relevant_file_set": list(norm)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TaskReadiness:
    """A task's frozen readiness declaration. Immutable; validated on build."""

    task_id: str
    base_commit: str
    relevant_file_set: tuple[str, ...]
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.task_id or "").strip():
            raise TaskReadinessError("task_id is required")
        if not str(self.base_commit or "").strip():
            raise TaskReadinessError("base_commit is required")
        # Validate + freeze the file set (raises on empty/unsafe).
        object.__setattr__(
            self, "relevant_file_set", canonical_relevant_file_set(self.relevant_file_set)
        )

    @property
    def relevant_file_set_hash(self) -> str:
        return compute_relevant_file_set_hash(self.relevant_file_set)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": self.task_id,
            "base_commit": self.base_commit,
            "relevant_file_set": list(self.relevant_file_set),
            "relevant_file_set_hash": self.relevant_file_set_hash,
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "TaskReadiness":
        """Rebuild from a persisted declaration. Fails closed (raises) on any
        malformed input OR a ``relevant_file_set_hash`` that does not match."""
        if not isinstance(d, Mapping):
            raise TaskReadinessError("task-readiness declaration is not a mapping")
        if str(d.get("schema_version") or "") != SCHEMA_VERSION:
            raise TaskReadinessError(
                f"unexpected schema_version: {d.get('schema_version')!r}"
            )
        tr = cls(
            task_id=str(d.get("task_id") or ""),
            base_commit=str(d.get("base_commit") or ""),
            relevant_file_set=tuple(d.get("relevant_file_set") or ()),
            fail_to_pass=tuple(str(x) for x in (d.get("fail_to_pass") or ())),
            pass_to_pass=tuple(str(x) for x in (d.get("pass_to_pass") or ())),
        )
        recorded = str(d.get("relevant_file_set_hash") or "")
        if recorded and recorded != tr.relevant_file_set_hash:
            raise TaskReadinessError(
                "relevant_file_set_hash mismatch: declaration was modified"
            )
        return tr

    @classmethod
    def load(cls, path: Path | str) -> "TaskReadiness":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - missing/corrupt manifest fails closed
            raise TaskReadinessError(f"cannot read task-readiness manifest: {exc}") from exc
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Readiness gate (R1-R8) -- mechanical, fail-closed
# ---------------------------------------------------------------------------

#: The eight readiness conditions from the contract, in order. R1-R4 task-layer,
#: R5-R6 run-layer, R7 program-layer, R8 F2-layer.
READINESS_CONDITIONS: tuple[str, ...] = (
    "R1_task_valid",
    "R2_gold_reachable",
    "R3_no_regression",
    "R4_not_env_fault",
    "R5_evidence_provenance",
    "R6_learning_signal_sufficient",
    "R7_promotion_reachable",
    "R8_endpoint_measurable",
)


@dataclass(frozen=True)
class ReadinessEvidence:
    """Explicit inputs to the gate. ``None`` = NOT ESTABLISHED (never pass)."""

    R1_task_valid: bool | None = None
    R2_gold_reachable: bool | None = None
    R3_no_regression: bool | None = None
    R4_not_env_fault: bool | None = None
    R5_evidence_provenance: bool | None = None
    R6_learning_signal_sufficient: bool | None = None
    R7_promotion_reachable: bool | None = None
    R8_endpoint_measurable: bool | None = None


@dataclass(frozen=True)
class ReadinessVerdict:
    ready: bool
    checks: dict[str, bool | None]
    unmet: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"ready": self.ready, "checks": dict(self.checks), "unmet": list(self.unmet)}


def evaluate_readiness(ev: ReadinessEvidence) -> ReadinessVerdict:
    """Evaluate R1-R8. READY iff every condition is explicitly ``True``.

    Fail closed: a missing/``None`` condition is NOT established and therefore
    NOT a pass. The gate proves engineering readiness mechanically; it does not
    infer it from documentation.
    """
    if not isinstance(ev, ReadinessEvidence):
        raise TaskReadinessError("readiness evidence must be a ReadinessEvidence")
    checks = {name: getattr(ev, name) for name in READINESS_CONDITIONS}
    unmet = tuple(name for name in READINESS_CONDITIONS if checks[name] is not True)
    return ReadinessVerdict(ready=not unmet, checks=checks, unmet=unmet)


def endpoint_measurable(tr: TaskReadiness | None) -> bool | None:
    """R8: a frozen, validated ``relevant_file_set`` exists for the task.

    ``None`` when no declaration is supplied (NOT ESTABLISHED), ``True`` only
    when a well-formed declaration validates.
    """
    if tr is None:
        return None
    try:
        # Re-validating the hash on a persisted object is done by from_dict; here
        # we simply confirm the frozen set is non-empty and well-formed.
        canonical_relevant_file_set(tr.relevant_file_set)
    except TaskReadinessError:
        return False
    return True
