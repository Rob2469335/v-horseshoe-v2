"""S8 job record: a versioned, fail-closed record of a **planned** S8 job.

Why this module exists
----------------------
``qwen_train.f2_evidence`` models *execution-derived* S8 evidence (base fails,
gold passes) and refuses everything else.  Nothing in the repository models the
earlier stage: "a job was **planned** for this task, from this pinned population
artifact, in this phase".  Without that, planning output has nowhere to live
that is (a) versioned, (b) identity-bound, and (c) structurally incapable of
being mistaken for execution evidence.

This module supplies that record.  It is deliberately **not** an evidence
verifier and can never make a task admissible:

* :class:`JobRecord.is_execution_evidence` is a property that is always
  ``False``.
* :func:`validate_job_record` returns ``ok=True`` only for *record integrity and
  well-formedness*.  It never returns S8 evidence, never reports a task as
  executed, passed, failed, or scientifically clean, and never reaches
  ``qwen_train.f2_evidence``'s ``VERIFIED`` state.

Nothing here executes anything.  ``test_cmd`` and every other task-supplied
string is treated as **inert data**: it is hashed, never invoked, never
interpolated into a shell, import path, or module name.

Authority
---------
Additive only.  This file does not modify, import-behaviourally override, or
weaken ``f2_evidence``, ``f2_governance``, ``f2_population``, ``f2_readiness``
or ``f2_preflight``.  Admission, contamination classification and scientific
validity remain where the governing authority put them.

Schema (``f2_s8_job_record_v1``)
-------------------------------
Canonical JSON (sorted keys, compact separators, ``ensure_ascii=False``); the
digest excludes ``record_digest`` itself, so it is never self-referential.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

__all__ = [
    "SCHEMA_VERSION",
    "JOB_STATUSES",
    "PHASES",
    "ALLOWED_TRANSITIONS",
    "REASON_CODES",
    "JobRecord",
    "JobValidation",
    "derive_job_id",
    "canonical_bytes",
    "build_job_record",
    "validate_job_record",
    "JobRecordError",
]

SCHEMA_VERSION = "f2_s8_job_record_v1"

#: Terminal statuses may not move.  ``planned -> completed`` is refused on
#: purpose: a job cannot be completed without having been attempted.
JOB_STATUSES = (
    "planned",
    "attempted",
    "completed",
    "failed",
    "rejected",
    "not_established",
)

#: Requested operation / phase of the job.  Purely a declaration of intent.
PHASES = ("PLAN", "ACQUIRE", "EXECUTE_S8", "VALIDATE", "ADMIT")

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "planned": ("attempted", "rejected", "not_established"),
    "attempted": ("completed", "failed", "rejected", "not_established"),
    "completed": (),
    "failed": ("attempted", "rejected", "not_established"),
    "rejected": (),
    "not_established": (),
}

#: Machine-readable rejection reasons.  Every failure carries at least one.
REASON_CODES = (
    "MISSING_FIELD",
    "WRONG_SCHEMA_VERSION",
    "MALFORMED_FIELD",
    "WRONG_TASK_ID",
    "WRONG_JOB_ID",
    "SOURCE_MISMATCH",
    "DIGEST_MISMATCH",
    "REPLAYED_JOB_ID",
    "INVALID_STATUS",
    "INVALID_STATUS_TRANSITION",
    "DUPLICATE_RECORD",
    "CROSS_SOURCE_INCONSISTENT",
    "PLAN_PRESENTED_AS_EXECUTION",
    "PROVENANCE_ABSENT",
    "CLEAN_NOT_ASSERTABLE_BY_JOB_RECORD",
    "UNTRUSTED_FIELD_VALUE",
)

_REQUIRED_FIELDS = (
    "schema_version",
    "job_id",
    "task_id",
    "source",
    "source_record_digest",
    "population_artifact",
    "phase",
    "status",
    "created_at",
    "provenance",
    "metadata_facts",
    "execution_state",
)

_HEX = set("0123456789abcdef")


class JobRecordError(ValueError):
    """The record is malformed or refuses a structural invariant."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Repository-canonical JSON bytes (sorted keys, compact, UTF-8)."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def derive_job_id(
    *,
    task_id: str,
    source: str,
    population_artifact_sha256: str,
    phase: str,
) -> str:
    """Deterministic job identity.

    Deriving rather than randomising makes a job **replay-detectable**: the same
    (task, source, pinned population, phase) always yields the same job id, so a
    second delivery of that job is a replay and not a new observation.
    """
    basis = "|".join(
        ("f2_s8_job_id_v1", str(task_id), str(source),
         str(population_artifact_sha256), str(phase))
    )
    return _sha256(basis)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class JobRecord:
    """An immutable planned-S8 job record.  Never execution evidence."""

    payload: dict[str, Any]

    @property
    def is_execution_evidence(self) -> bool:
        """Always ``False``: a job record is never S8 execution evidence."""
        return False

    @property
    def s8_evidence_state(self) -> str:
        """Always ``not_established`` — only ``f2_evidence`` can say otherwise."""
        return "not_established"

    @property
    def task_id(self) -> str:
        return str(self.payload.get("task_id") or "")

    @property
    def job_id(self) -> str:
        return str(self.payload.get("job_id") or "")

    @property
    def status(self) -> str:
        return str(self.payload.get("status") or "")

    @property
    def phase(self) -> str:
        return str(self.payload.get("phase") or "")

    @property
    def source(self) -> str:
        return str(self.payload.get("source") or "")

    @property
    def record_digest(self) -> str:
        return str(self.payload.get("record_digest") or "")

    def compute_digest(self) -> str:
        body = {k: v for k, v in self.payload.items() if k != "record_digest"}
        return _sha256(canonical_bytes(body).decode("utf-8"))

    @property
    def metadata_facts_digest(self) -> str:
        return _sha256(canonical_bytes(self.payload.get("metadata_facts") or {}).decode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def build_job_record(
    *,
    task_id: str,
    source: str,
    source_record_digest: str,
    population_artifact: Mapping[str, Any],
    phase: str,
    status: str = "planned",
    metadata_facts: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> JobRecord:
    """Build a well-formed job record for a **planned** task.

    Refuses, rather than silently repairing: an unknown phase or status, a
    non-SHA-256 source record digest, a population artifact without both a name
    and a SHA-256, a status of ``completed`` that is not preceded by an
    attempt, or a ``CLEAN`` contamination claim.  Nothing here reads a task
    command.
    """
    if phase not in PHASES:
        raise JobRecordError(f"unknown phase {phase!r}; known: {PHASES}")
    if status not in JOB_STATUSES:
        raise JobRecordError(f"unknown status {status!r}; known: {JOB_STATUSES}")
    if status in ("completed", "failed"):
        raise JobRecordError(
            f"a job record may not be BUILT with status {status!r}: both assert an "
            "execution outcome, and this module never performs an execution. "
            "Such a record can only come from an authorized execution producer."
        )
    if not _is_sha256(source_record_digest):
        raise JobRecordError("source_record_digest must be a 64-hex SHA-256")
    art_name = str((population_artifact or {}).get("name") or "")
    art_sha = str((population_artifact or {}).get("sha256") or "")
    if not art_name or not _is_sha256(art_sha):
        raise JobRecordError(
            "population_artifact requires a name and a 64-hex sha256"
        )
    facts = dict(metadata_facts or {})
    if "contamination_class" in facts:
        raise JobRecordError(
            "a job record never asserts a contamination class; CLEAN/POTENTIALLY/"
            "UNKNOWN are decided by f2_population.screen_entry with real evidence"
        )
    if "test_cmd" in facts:
        raise JobRecordError(
            "metadata_facts must not carry a raw test_cmd; store test_cmd_sha256 — "
            "task commands are untrusted data and are never propagated as commands"
        )
    prov = dict(provenance or {})
    if not prov:
        raise JobRecordError("provenance is required and may not be empty")

    job_id = derive_job_id(
        task_id=str(task_id),
        source=str(source),
        population_artifact_sha256=art_sha,
        phase=str(phase),
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "task_id": str(task_id),
        "source": str(source),
        "source_record_digest": str(source_record_digest),
        "population_artifact": {"name": art_name, "sha256": art_sha},
        "phase": str(phase),
        "status": str(status),
        "created_at": str(created_at or _utc_now()),
        "provenance": prov,
        "metadata_facts": facts,
        # Execution-derived facts are structurally absent for a planned job.
        "execution_state": "not_established",
        "execution_facts": {},
        "record_digest": "",
    }
    rec = JobRecord(payload=payload)
    payload["record_digest"] = rec.compute_digest()
    return JobRecord(payload=payload)


def _fail(*codes: str) -> JobValidation:
    return JobValidation(ok=False, reason_codes=list(codes))


@dataclass(frozen=True)
class JobValidation:
    """Well-formedness verdict for a job record.

    ``ok`` means "this record is a structurally valid **job record**".  It never
    means the task executed, passed, is admissible, or is scientifically clean.
    """

    ok: bool
    reason_codes: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def is_execution_evidence(self) -> bool:
        return False

    def __bool__(self) -> bool:  # never let `if validation:` read as S8 truth
        return self.ok


def validate_job_record(
    record: Mapping[str, Any] | JobRecord,
    *,
    expected_task_id: str | None = None,
    expected_source: str | None = None,
    expected_population_sha256: str | None = None,
    seen_job_ids: Sequence[str] | set[str] | None = None,
    seen_records: Mapping[str, str] | None = None,
    previous_status: str | None = None,
    known_source_digests: Mapping[str, str] | None = None,
) -> JobValidation:
    """Fail-closed structural validation of a job record.

    Checks, in order: required fields, schema version, field shapes, derived job
    identity, task/source/pinned-population identity, record digest, replay and
    duplicate job ids, status legality and transition legality, the
    plan-presented-as-execution rule, provenance presence, cross-source
    consistency, untrusted values, and the prohibition on asserting cleanliness.
    """
    payload = record.to_dict() if isinstance(record, JobRecord) else dict(record or {})
    if not payload:
        return _fail("MISSING_FIELD")

    missing = [k for k in _REQUIRED_FIELDS if k not in payload]
    if missing:
        return _fail("MISSING_FIELD")

    if payload.get("schema_version") != SCHEMA_VERSION:
        return _fail("WRONG_SCHEMA_VERSION")

    task_id = payload.get("task_id")
    source = payload.get("source")
    phase = payload.get("phase")
    status = payload.get("status")
    if not isinstance(task_id, str) or not task_id.strip():
        return _fail("MALFORMED_FIELD")
    if not isinstance(source, str) or not source.strip():
        return _fail("MALFORMED_FIELD")
    if phase not in PHASES:
        return _fail("MALFORMED_FIELD")
    if status not in JOB_STATUSES:
        return _fail("INVALID_STATUS")
    if not isinstance(payload.get("created_at"), str) or not payload["created_at"]:
        return _fail("MALFORMED_FIELD")
    if not isinstance(payload.get("provenance"), dict) or not payload["provenance"]:
        return _fail("PROVENANCE_ABSENT")
    for key in ("source_record_digest",):
        if not _is_sha256(payload.get(key)):
            return _fail("MALFORMED_FIELD")
    art = payload.get("population_artifact")
    if not isinstance(art, dict) or not _is_sha256(art.get("sha256")):
        return _fail("MALFORMED_FIELD")

    # --- cleanliness may never be asserted by a job record -------------------
    facts = payload.get("metadata_facts")
    if not isinstance(facts, dict):
        return _fail("MALFORMED_FIELD")
    if "contamination_class" in facts or payload.get("asserted_contamination_class"):
        return _fail("CLEAN_NOT_ASSERTABLE_BY_JOB_RECORD")
    if "test_cmd" in facts:
        return _fail("UNTRUSTED_FIELD_VALUE")

    # --- plan presented as execution ----------------------------------------
    exec_state = payload.get("execution_state")
    exec_facts = payload.get("execution_facts")
    if exec_state != "not_established":
        return _fail("PLAN_PRESENTED_AS_EXECUTION")
    if exec_facts not in ({}, None):
        return _fail("PLAN_PRESENTED_AS_EXECUTION")
    if status in ("planned", "attempted") and payload.get("executed") is True:
        return _fail("PLAN_PRESENTED_AS_EXECUTION")

    # --- identity ------------------------------------------------------------
    if expected_task_id is not None and task_id != expected_task_id:
        return _fail("WRONG_TASK_ID")
    if expected_source is not None and source != expected_source:
        return _fail("SOURCE_MISMATCH")
    art_sha = str(art.get("sha256"))
    if (
        expected_population_sha256 is not None
        and art_sha != expected_population_sha256
    ):
        return _fail("SOURCE_MISMATCH")
    derived = derive_job_id(
        task_id=task_id,
        source=source,
        population_artifact_sha256=art_sha,
        phase=str(phase),
    )
    if payload.get("job_id") != derived:
        return _fail("WRONG_JOB_ID")

    # --- integrity -----------------------------------------------------------
    claimed_digest = str(payload.get("record_digest") or "")
    body = {k: v for k, v in payload.items() if k != "record_digest"}
    actual = _sha256(canonical_bytes(body).decode("utf-8"))
    if not _is_sha256(claimed_digest) or claimed_digest != actual:
        return _fail("DIGEST_MISMATCH")

    # --- replay / duplicate --------------------------------------------------
    # Re-delivery of the SAME bytes for a known job id is a duplicate; a
    # DIFFERENT payload under a known job id is a replay attempt (identity
    # re-use).  Both are refused; neither is a new observation.
    job_id = str(payload["job_id"])
    if seen_records is not None and job_id in seen_records:
        if str(seen_records[job_id]) == claimed_digest:
            return _fail("DUPLICATE_RECORD")
        return _fail("REPLAYED_JOB_ID")
    if seen_job_ids is not None and job_id in set(seen_job_ids):
        return _fail("REPLAYED_JOB_ID")

    # --- status transition ---------------------------------------------------
    if previous_status is not None:
        if previous_status not in JOB_STATUSES:
            return _fail("INVALID_STATUS")
        if status not in ALLOWED_TRANSITIONS.get(previous_status, ()):
            return _fail("INVALID_STATUS_TRANSITION")

    # --- cross-source consistency -------------------------------------------
    if known_source_digests is not None:
        prior = known_source_digests.get(task_id)
        if prior is not None and prior != payload["source_record_digest"]:
            return _fail("CROSS_SOURCE_INCONSISTENT")

    return JobValidation(ok=True, reason_codes=[], detail="job record is well formed")
