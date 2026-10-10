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
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "ej-task-readiness/1"

#: Characters refused inside a repo-relative path: the characters Windows forbids
#: in a file name (``<>:"|?*`` — ``:`` is additionally caught by the structural
#: rule below, ``\`` is normalised to ``/`` first) plus the shell separator and
#: redirection characters ``;`` and ``&``.  This is a DENYLIST on purpose.
#:
#: It replaces an allowlist, ``[A-Za-z0-9_./\\-]+``, whose own comment claimed to
#: reject "characters that are not path characters" while rejecting characters
#: that ARE path characters: space, ``@``, ``+``, ``[``/``]``, ``(``/``)``,
#: ``{``/``}``, ``$``, the backtick and ``'``.  Every one of those occurs in real
#: upstream repository paths (``Release Notes/511.md``,
#: ``packages/@node-red/...``, ``src/Moq/Mock`1.cs``,
#: ``docs/Euler's Totient/index.md``, ``{{ cookiecutter.repo_name }}/...``), so
#: the allowlist silently refused 236 eligible tasks at screen S6 (measured on
#: the two-source F2 union census) purely because of their file names.
_UNSAFE_PATH_CHARS = frozenset('<>:"|?*;&')

#: Unicode categories that can never be part of a usable path: control (Cc),
#: format (Cf — includes the RTL-override characters used to spoof file names),
#: surrogate (Cs), private use (Co) and the line/paragraph separators (Zl, Zp).
_UNSAFE_PATH_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Zl", "Zp"})


class TaskReadinessError(ValueError):
    """A task-readiness declaration is absent, malformed, or inconsistent."""


class ReadinessGateError(TaskReadinessError):
    """The readiness gate refused to let an experiment arm proceed.

    Raised by the F2 orchestrator pre-flight when readiness is absent, malformed,
    identity-mismatched, or not fully met. Fail-closed: no arm may execute after
    this is raised.
    """


def _has_unsafe_character(text: str) -> bool:
    return any(
        ch in _UNSAFE_PATH_CHARS
        or unicodedata.category(ch) in _UNSAFE_PATH_CATEGORIES
        for ch in text
    )


def _normalize_path(p: object) -> str:
    if not isinstance(p, str):
        raise TaskReadinessError(f"relevant_file_set entry is not a string: {p!r}")
    norm = p.strip().replace("\\", "/")
    if not norm:
        raise TaskReadinessError("relevant_file_set entry is empty")
    if norm.startswith("/") or ":" in norm or ".." in PurePosixPath(norm).parts:
        raise TaskReadinessError(f"unsafe relevant_file_set entry: {p!r}")
    if _has_unsafe_character(norm):
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

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "ReadinessEvidence":
        """Build from a mapping. Any non-boolean / absent condition stays ``None``
        (NOT ESTABLISHED) — it is never coerced to a pass."""
        if d is None:
            return cls()
        if not isinstance(d, Mapping):
            raise TaskReadinessError("readiness evidence must be a mapping")
        return cls(
            **{
                name: (d.get(name) if d.get(name) in (True, False) else None)
                for name in READINESS_CONDITIONS
            }
        )

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in READINESS_CONDITIONS}


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


def manifest_readiness_payload(
    tr: TaskReadiness,
    evidence: ReadinessEvidence,
    verdict: ReadinessVerdict,
) -> dict:
    """The canonical readiness payload frozen INTO the F2 manifest.

    The manifest is the only integrity anchor that crosses the process boundary
    (worker/adapter receive a manifest path, not trusted memory), so the
    readiness gate is enforced from manifest-verified data, never from a
    caller-supplied ``ready=True``.
    """
    if not isinstance(tr, TaskReadiness):
        raise TaskReadinessError("declaration must be a TaskReadiness")
    if not isinstance(evidence, ReadinessEvidence):
        raise TaskReadinessError("evidence must be a ReadinessEvidence")
    if not isinstance(verdict, ReadinessVerdict):
        raise TaskReadinessError("verdict must be a ReadinessVerdict")
    # Normalize R8 in the recorded evidence so it always equals what the
    # manifest-bound gate will recompute (endpoint_measurable(tr)).
    evidence_norm = replace(evidence, R8_endpoint_measurable=endpoint_measurable(tr))
    return {
        "declaration": tr.to_dict(),
        "evidence": evidence_norm.to_dict(),
        "verdict": verdict.to_dict(),
    }


def enforce_readiness_from_manifest_payload(
    payload: Mapping[str, Any] | None,
    *,
    task_id: str,
    base_commit: str = "",
) -> tuple[TaskReadiness, ReadinessVerdict]:
    """Fail-closed readiness enforcement from a MANIFEST-BOUND payload.

    The R1-R8 logic is ``evaluate_readiness`` (the single canonical
    implementation); this wrapper only changes WHERE the inputs come from: a
    verified frozen manifest rather than a caller's memory. It therefore
    independently reconstructs the verdict — a caller cannot inject
    ``ready=True`` or a forged verdict, because the recorded verdict must
    agree with the recomputed one.

    Raises ``ReadinessGateError`` on: absent/empty payload, malformed
    declaration, hash mismatch, identity mismatch, or any R1-R8 not explicitly
    ``True``.
    """
    if not isinstance(payload, Mapping) or not payload:
        raise ReadinessGateError(
            "frozen manifest carries no task-readiness declaration: F2 cannot proceed"
        )
    declaration = payload.get("declaration")
    evidence_raw = payload.get("evidence")
    recorded = payload.get("verdict")

    tr = TaskReadiness.from_dict(declaration)
    # Identity binding to the manifest the caller already verified.
    if task_id and tr.task_id != task_id:
        raise ReadinessGateError(
            f"readiness task_id {tr.task_id!r} != manifest task_id {task_id!r}"
        )
    if base_commit and tr.base_commit != base_commit:
        raise ReadinessGateError(
            f"readiness base_commit {tr.base_commit!r} != expected {base_commit!r}"
        )
    ev = ReadinessEvidence.from_dict(evidence_raw)
    # R8 is derived from the declaration; a manifest cannot assert it.
    ev = replace(ev, R8_endpoint_measurable=endpoint_measurable(tr))
    verdict = evaluate_readiness(ev)
    if not verdict.ready:
        raise ReadinessGateError(
            "task not READY (unmet: " + ", ".join(verdict.unmet) + ")"
        )
    if isinstance(recorded, Mapping):
        rec_ready = recorded.get("ready")
        rec_unmet = tuple(recorded.get("unmet") or ())
        if rec_ready is not True or rec_unmet != verdict.unmet:
            raise ReadinessGateError(
                "recorded readiness verdict disagrees with the recomputed gate"
            )
    return tr, verdict
