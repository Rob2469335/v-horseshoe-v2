"""F2-S8 run-level state-set aggregate (``f2-s8-state-set/v1``).

Why this module exists
----------------------
The frozen S8 governed path requires exactly TWO run-level digests --
``SWARM_S8_BASE_STATE_DIGEST`` and ``SWARM_S8_GOLD_STATE_DIGEST``
(``f2_s8_compose.S8_GOVERNANCE_ENV``) -- while the Stage-1 measurement contract
(``f2_execution_state``) records one measured state PER TASK and SIDE.  Mapping
the per-record measurements onto the two run-level slots is the aggregate this
module defines.  It was delegated as an engineering decision (F2-IMPL-AUTH-038)
after the owner's 2026-10-17 directive retired the earlier "owner decides" hold.

Design rationale (sources: SLSA v1.2, in-toto Statement v1, RFC 8785, SP 800-218)
--------------------------------------------------------------------------------
* **Versioned type identifier inside the preimage** -- SLSA v1.2 has no normative
  raw-digest domain-separation convention; the established pattern is a *versioned
  type identifier authenticated inside the signed/typed structure*
  (https://slsa.dev/spec/v1.2/attestation-model; in-toto Statement v1
  ``predicateType``).  This aggregate therefore embeds its own
  ``schema_version`` (``f2-s8-state-set/v1``) and a ``type`` URI inside the
  hashed payload, so its digest cannot be confused with any other digest type
  (per-record ``measured_state_digest``, manifest digests, ...).  NIST SP 800-218
  PS.2.1/PS.3.2 (integrity-verification and provenance-recording tasks) are the
  SSDF anchors for publishing and binding such verification data.
* **Canonical serialization** -- ``json.dumps(sort_keys=True,
  separators=(",", ":"), ensure_ascii=False).encode("utf-8")`` is the repository
  convention (``f2_evidence._canonical_bytes``, ``f2_authorization.canonical_bytes``).
  For the restricted payload used here (object keys are ASCII; values are ASCII
  strings, non-negative integers, or lists of such strings) Python's sort order
  coincides with RFC 8785's UTF-16 code-unit ordering and its string escaping,
  so the preimage is RFC-8785-equivalent on this subset.  Numbers are confined to
  non-negative integers below 2**53 (exactly representable; RFC 8785
  serializes them identically).  A payload containing anything else is rejected
  fail-closed rather than silently serialized.
* **Binding** -- the aggregate is bound to: the CANDIDATE MANIFEST digest (the
  exact built set), the SIDE, the measurement-campaign label, the EXPLICIT SET
  of per-record environment identities, the evaluator implementation digest,
  and -- per entry, in sorted order -- the task identity, the side, the task's
  measured state digest and the task's full ``f2-execution-state/v1`` record
  digest.  Records cannot be swapped between tasks, sides or manifests without
  changing the aggregate.  Per-task environment identity is genuinely
  HETEROGENEOUS in this candidate set (upstream projects pin different Python
  minors: 3.10.11 / 3.13.1 / 3.14.8), so the aggregate binds the uniform
  campaign label at top level and enumerates the distinct per-record
  environment identities rather than averaging them into a false uniformity.
* **Fail-closed** -- missing, duplicate, malformed, unexpected (not in the
  manifest), manifest-mismatched, non-``ES_VERIFIED`` or side-mismatched records
  refuse the aggregate with a named condition; nothing is dropped silently.

What the aggregate IS and IS NOT
--------------------------------
IS:  a deterministic commitment to the SET of measured staged states of one
     side, for one candidate manifest, derived only from records that individually
     verify under ``f2-execution-state/v1``.
IS NOT: a per-execution attestation.  The baseline records it aggregates carry
     deterministic run nonces and therefore no replay protection (see
     ``f2_s8_measure_states``); the authoritative per-execution records are
     produced in the guest at execution time (proposal Stage-1/Stage-2).  The
     aggregate also does not prove runtime or hardware identity: it commits to
     source-tree, dependency-set and evaluator-implementation measurements, not
     to VM resource configuration.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):  # pragma: no cover - direct execution
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qwen_train.f2_execution_state import (  # noqa: E402
    SIDE_BASE,
    SIDE_GOLD,
    VALID_SIDES,
    verify_execution_state,
)

__all__ = [
    "SCHEMA_VERSION",
    "TYPE_URI",
    "AGGREGATE_STATES",
    "SS_VERIFIED",
    "SS_INPUT_MISSING",
    "SS_INPUT_MALFORMED",
    "SS_UNKNOWN_SCHEMA",
    "SS_MANIFEST_MISMATCH",
    "SS_TASK_SET_MISMATCH",
    "SS_RECORD_INVALID",
    "SS_DUPLICATE_RECORD",
    "SS_SIDE_MISMATCH",
    "SS_ENVIRONMENT_MISMATCH",
    "SS_EVALUATOR_MISMATCH",
    "SS_AGGREGATE_MISMATCH",
    "SS_NONCANONICAL_INPUT",
    "StateSetError",
    "StateSetAggregate",
    "StateSetVerification",
    "build_state_set_aggregate",
    "verify_state_set_aggregate",
    "main",
]

SCHEMA_VERSION = "f2-s8-state-set/v1"
#: Domain separation: a versioned type URI, embedded IN the hashed payload.
#: Verifiers pin this (SLSA v1.2 attestation-model pattern: the type identifier
#: is authenticated inside the structure, not implied by context).
TYPE_URI = "urn:swarm-os:f2-s8:execution-state-set:1"

SS_VERIFIED = "SS_VERIFIED"
SS_INPUT_MISSING = "SS_INPUT_MISSING"
SS_INPUT_MALFORMED = "SS_INPUT_MALFORMED"
SS_UNKNOWN_SCHEMA = "SS_UNKNOWN_SCHEMA"
SS_MANIFEST_MISMATCH = "SS_MANIFEST_MISMATCH"
SS_TASK_SET_MISMATCH = "SS_TASK_SET_MISMATCH"
SS_RECORD_INVALID = "SS_RECORD_INVALID"
SS_DUPLICATE_RECORD = "SS_DUPLICATE_RECORD"
SS_SIDE_MISMATCH = "SS_SIDE_MISMATCH"
SS_ENVIRONMENT_MISMATCH = "SS_ENVIRONMENT_MISMATCH"
SS_EVALUATOR_MISMATCH = "SS_EVALUATOR_MISMATCH"
SS_AGGREGATE_MISMATCH = "SS_AGGREGATE_MISMATCH"
SS_NONCANONICAL_INPUT = "SS_NONCANONICAL_INPUT"

AGGREGATE_STATES = (
    SS_VERIFIED,
    SS_INPUT_MISSING,
    SS_INPUT_MALFORMED,
    SS_UNKNOWN_SCHEMA,
    SS_MANIFEST_MISMATCH,
    SS_TASK_SET_MISMATCH,
    SS_RECORD_INVALID,
    SS_DUPLICATE_RECORD,
    SS_SIDE_MISMATCH,
    SS_ENVIRONMENT_MISMATCH,
    SS_EVALUATOR_MISMATCH,
    SS_AGGREGATE_MISMATCH,
    SS_NONCANONICAL_INPUT,
)

#: The derivation schema this aggregate consumes (``f2_s8_measure_states``).
DERIVATION_SCHEMA_VERSION = "f2-s8-state-derivation/v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StateSetError(RuntimeError):
    """The aggregate could not be built (fail closed)."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: Any) -> bool:
    return bool(_SHA256_RE.match(str(value or "")))


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Repository-canonical JSON; RFC-8785-equivalent on this payload subset.

    The subset is enforced by :func:`_check_payload_subset`: ASCII keys, and
    values that are ASCII strings, non-negative exact integers, or lists
    thereof.  Anything else raises instead of being serialized under an
    unverified canonicalization claim.
    """
    _check_payload_deep(payload)
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _check_payload_subset(value: Any, *, path: str = "$") -> None:
    """Reject anything outside the RFC-8785-equivalent subset (fail closed)."""
    if isinstance(value, str):
        try:
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise StateSetError(
                f"non-ASCII string at {path}: the aggregate preimage is confined "
                "to the ASCII subset where Python's canonical JSON coincides with "
                "RFC 8785; refusing rather than claiming an unverified "
                "canonicalization"
            ) from exc
        return
    if isinstance(value, bool) or not isinstance(value, int):
        # bool is an int subclass but is NOT an exact-integer JSON number here.
        raise StateSetError(
            f"unsupported value at {path}: {type(value).__name__}; only ASCII "
            "strings, non-negative exact integers and lists thereof are admitted"
        )
    if value < 0 or value >= 2**53:
        raise StateSetError(
            f"integer at {path} is outside the exactly-representable range"
        )
    return


def _check_payload_deep(value: Any, *, path: str = "$") -> None:
    """Full recursive subset check including list elements and dict keys."""
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_payload_deep(item, path=f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise StateSetError(f"non-string key at {path}: {type(key).__name__}")
            _check_payload_subset(key, path=f"{path}.<key>")
            _check_payload_deep(item, path=f"{path}.{key}")
        return
    _check_payload_subset(value, path=path)


@dataclass(frozen=True)
class StateSetAggregate:
    """One side's run-level aggregate over the verified per-task records."""

    schema_version: str
    type: str
    side: str
    candidate_manifest_digest: str
    #: The uniform measurement-campaign label (``derivation.environment_label``).
    #: Per-TASK environment identity (interpreter version differs: some upstream
    #: projects pin 3.10/3.13, others 3.14) is bound per-entry through the record
    #: digest AND explicitly enumerated in ``environment_identities``.
    environment_identity: str
    #: Sorted distinct per-record environment identities: heterogeneity is made
    #: visible and bound, never averaged away.
    environment_identities: tuple[str, ...]
    evaluator_implementation_digest: str
    task_count: int
    #: (task_id, side, measured_state_digest, execution_state_digest), sorted.
    entries: tuple[tuple[str, str, str, str], ...]
    aggregate_digest: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": self.type,
            "side": self.side,
            "candidate_manifest_digest": self.candidate_manifest_digest,
            "environment_identity": self.environment_identity,
            "environment_identities": list(self.environment_identities),
            "evaluator_implementation_digest": self.evaluator_implementation_digest,
            "task_count": int(self.task_count),
            "entries": [
                {
                    "task_id": task_id,
                    "side": side,
                    "measured_state_digest": measured,
                    "execution_state_digest": record,
                }
                for task_id, side, measured, record in self.entries
            ],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.payload())

    def compute_digest(self) -> str:
        return _sha256(self.canonical_bytes())

    def bind(self) -> "StateSetAggregate":
        return StateSetAggregate(
            **{**self.__dict__, "aggregate_digest": self.compute_digest()}
        )

    def to_dict(self) -> dict[str, Any]:
        d = self.payload()
        d["aggregate_digest"] = self.aggregate_digest
        return d


@dataclass(frozen=True)
class StateSetVerification:
    """Result of verifying one aggregate (only ``SS_VERIFIED`` accepts)."""

    state: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == SS_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "detail": self.detail}


def _fail(state: str, detail: str) -> StateSetVerification:
    return StateSetVerification(state=state, detail=detail)


def _record_for_side(
    result: Mapping[str, Any], side: str, task_id: str
) -> tuple[Mapping[str, Any] | None, StateSetVerification | None]:
    raw = result.get(side)
    if raw is None:
        return None, _fail(
            SS_RECORD_INVALID, f"{task_id}: no {side}-side record in the derivation"
        )
    if not isinstance(raw, Mapping):
        return None, _fail(
            SS_RECORD_INVALID, f"{task_id}: {side}-side record is not an object"
        )
    return raw, None


def build_state_set_aggregate(
    *,
    derivation: Mapping[str, Any] | None,
    candidate_manifest_digest: str,
    side: str,
    only: Sequence[str] = (),
) -> StateSetAggregate:
    """Derive one side's run-level aggregate, fail-closed.

    ``derivation`` is the ``f2-s8-state-derivation/v1`` document produced by
    ``f2_s8_measure_states`` (the per-task, per-side ``f2-execution-state/v1``
    records).  ``candidate_manifest_digest`` must be the SHA-256 of the EXACT
    build-manifest bytes the records were derived from; the derivation document
    carries its own record of that digest and a mismatch refuses the aggregate.

    Every record is individually re-verified under ``f2-execution-state/v1``
    (measurement integrity, role, schema, algorithm) before it may contribute;
    a record that does not verify makes the whole aggregate fail rather than
    being skipped.
    """
    if side not in VALID_SIDES:
        raise StateSetError(f"unknown side {side!r}; expected one of {VALID_SIDES}")
    if derivation is None:
        raise StateSetError("no derivation document supplied")
    if not isinstance(derivation, Mapping):
        raise StateSetError("derivation document is not an object")
    if not _is_sha256(candidate_manifest_digest):
        raise StateSetError("candidate_manifest_digest is not a 64-hex SHA-256")

    schema = str(derivation.get("schema_version") or "")
    if schema != DERIVATION_SCHEMA_VERSION:
        raise StateSetError(
            f"unknown derivation schema_version {schema!r} "
            f"(expected {DERIVATION_SCHEMA_VERSION!r})"
        )
    derived = str(derivation.get("candidate_manifest_digest") or "")
    if derived != candidate_manifest_digest:
        raise StateSetError(
            "the derivation document is bound to candidate manifest "
            f"{derived[:12]}… but the aggregate was requested for "
            f"{candidate_manifest_digest[:12]}… (SS_MANIFEST_MISMATCH)"
        )

    raw_results = derivation.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise StateSetError("the derivation document contains no results")

    wanted = set(str(t) for t in only) if only else None
    evaluator_digest: str | None = None
    env_identities: set[str] = set()
    seen: set[str] = set()
    entries: list[tuple[str, str, str, str]] = []

    for raw in raw_results:
        if not isinstance(raw, Mapping):
            raise StateSetError("a derivation result is not an object")
        task_id = str(raw.get("task_id") or "")
        if not task_id:
            raise StateSetError("a derivation result has no task_id")
        if wanted is not None and task_id not in wanted:
            continue
        if task_id in seen:
            raise StateSetError(
                f"{task_id}: duplicate task entry in the derivation "
                "(SS_DUPLICATE_RECORD)"
            )
        seen.add(task_id)
        if str(raw.get("pair_state") or "") != "ES_VERIFIED":
            raise StateSetError(
                f"{task_id}: pair_state is {raw.get('pair_state')!r}, not "
                "'ES_VERIFIED'; only fully verified pairs may aggregate "
                "(SS_RECORD_INVALID)"
            )
        record, problem = _record_for_side(raw, side, task_id)
        if problem is not None:
            raise StateSetError(f"{problem.state}: {problem.detail}")

        verdict = verify_execution_state(
            record,
            expected_instance_id=task_id,
            expected_side=side,
        )
        if not verdict.ok:
            raise StateSetError(
                f"{task_id}: {side} record failed f2-execution-state/v1 "
                f"verification ({verdict.state}): {verdict.detail} "
                "(SS_RECORD_INVALID)"
            )

        env_identity = str(record.get("environment_identity") or "")
        eval_digest = str(record.get("evaluator_implementation_digest") or "")
        if not env_identity:
            raise StateSetError(
                f"{task_id}: record carries no environment_identity "
                "(SS_ENVIRONMENT_MISMATCH)"
            )
        env_identities.add(env_identity)
        if evaluator_digest is None:
            evaluator_digest = eval_digest
        elif eval_digest != evaluator_digest:
            raise StateSetError(
                f"{task_id}: evaluator_implementation_digest "
                f"{eval_digest[:12]}… != {evaluator_digest[:12]}…; one "
                "aggregate describes ONE evaluator "
                "(SS_EVALUATOR_MISMATCH)"
            )

        entries.append(
            (
                task_id,
                side,
                str(record.get("measured_state_digest") or ""),
                str(record.get("execution_state_digest") or ""),
            )
        )

    if not entries:
        raise StateSetError("no records selected for the aggregate")

    # Deterministic order: ascending task_id (ASCII; code-unit order coincides
    # with the canonical-JSON ordering used elsewhere).
    entries.sort(key=lambda e: e[0])

    label = str(derivation.get("environment_label") or "").strip()
    if not label:
        raise StateSetError(
            "the derivation document carries no environment_label; the aggregate "
            "binds the uniform measurement-campaign label at top level "
            "(SS_ENVIRONMENT_MISMATCH)"
        )

    return StateSetAggregate(
        schema_version=SCHEMA_VERSION,
        type=TYPE_URI,
        side=side,
        candidate_manifest_digest=candidate_manifest_digest,
        environment_identity=label,
        environment_identities=tuple(sorted(env_identities)),
        evaluator_implementation_digest=str(evaluator_digest or ""),
        task_count=len(entries),
        entries=tuple(entries),
    ).bind()


def verify_state_set_aggregate(
    aggregate: StateSetAggregate | Mapping[str, Any] | None,
    *,
    expected_side: str | None = None,
    expected_candidate_manifest_digest: str | None = None,
    expected_task_ids: Sequence[str] | None = None,
) -> StateSetVerification:
    """Verify a supplied aggregate fail-closed.

    Re-derives the digest from the canonical payload (a forged digest, a swapped
    entry, a reordered-but-different entry set or an edited field all fail), then
    checks the side, the manifest binding and -- when supplied -- the EXACT task
    set the aggregate must cover (missing or unexpected tasks refuse).
    """
    if aggregate is None:
        return _fail(SS_INPUT_MISSING, "no state-set aggregate supplied")
    if isinstance(aggregate, Mapping):
        try:
            entries_raw = aggregate.get("entries")
            if not isinstance(entries_raw, list):
                return _fail(SS_INPUT_MALFORMED, "entries is not a list")
            entries: list[tuple[str, str, str, str]] = []
            for item in entries_raw:
                if not isinstance(item, Mapping):
                    return _fail(SS_INPUT_MALFORMED, "an entry is not an object")
                entries.append(
                    (
                        str(item.get("task_id") or ""),
                        str(item.get("side") or ""),
                        str(item.get("measured_state_digest") or ""),
                        str(item.get("execution_state_digest") or ""),
                    )
                )
            aggregate = StateSetAggregate(
                schema_version=str(aggregate.get("schema_version") or ""),
                type=str(aggregate.get("type") or ""),
                side=str(aggregate.get("side") or ""),
                candidate_manifest_digest=str(
                    aggregate.get("candidate_manifest_digest") or ""
                ),
                environment_identity=str(aggregate.get("environment_identity") or ""),
                environment_identities=tuple(
                    str(x) for x in (aggregate.get("environment_identities") or [])
                ),
                evaluator_implementation_digest=str(
                    aggregate.get("evaluator_implementation_digest") or ""
                ),
                task_count=int(aggregate.get("task_count") or 0),
                entries=tuple(entries),
                aggregate_digest=str(aggregate.get("aggregate_digest") or ""),
            )
        except StateSetError as exc:
            return _fail(SS_NONCANONICAL_INPUT, str(exc))
        except (TypeError, ValueError) as exc:
            return _fail(SS_INPUT_MALFORMED, f"aggregate could not be parsed: {exc}")
    if not isinstance(aggregate, StateSetAggregate):
        return _fail(
            SS_INPUT_MALFORMED,
            f"unsupported aggregate type: {type(aggregate).__name__}",
        )

    if aggregate.schema_version != SCHEMA_VERSION:
        return _fail(
            SS_UNKNOWN_SCHEMA,
            f"unknown schema_version {aggregate.schema_version!r} "
            f"(expected {SCHEMA_VERSION!r})",
        )
    if aggregate.type != TYPE_URI:
        return _fail(
            SS_UNKNOWN_SCHEMA,
            f"unknown type {aggregate.type!r} (expected {TYPE_URI!r}); the "
            "digest is domain-separated by this identifier",
        )
    if aggregate.side not in VALID_SIDES:
        return _fail(SS_SIDE_MISMATCH, f"unknown side {aggregate.side!r}")
    if not _is_sha256(aggregate.candidate_manifest_digest):
        return _fail(
            SS_INPUT_MALFORMED, "candidate_manifest_digest is not a 64-hex digest"
        )
    if not aggregate.entries:
        return _fail(SS_INPUT_MALFORMED, "the aggregate carries no entries")
    if not aggregate.environment_identity:
        return _fail(SS_INPUT_MALFORMED, "the aggregate carries no environment label")
    if not aggregate.environment_identities or list(
        aggregate.environment_identities
    ) != sorted(aggregate.environment_identities):
        return _fail(
            SS_INPUT_MALFORMED,
            "environment_identities must be non-empty and sorted",
        )
    if aggregate.task_count != len(aggregate.entries):
        return _fail(
            SS_INPUT_MALFORMED,
            f"task_count {aggregate.task_count} != {len(aggregate.entries)} entries",
        )

    seen: set[str] = set()
    for task_id, entry_side, measured, record in aggregate.entries:
        if not task_id:
            return _fail(SS_INPUT_MALFORMED, "an entry has no task_id")
        if task_id in seen:
            return _fail(
                SS_DUPLICATE_RECORD, f"{task_id} appears more than once"
            )
        seen.add(task_id)
        if entry_side != aggregate.side:
            return _fail(
                SS_SIDE_MISMATCH,
                f"{task_id}: entry side {entry_side!r} != aggregate side "
                f"{aggregate.side!r}",
            )
        for label, value in (
            ("measured_state_digest", measured),
            ("execution_state_digest", record),
        ):
            if not _is_sha256(value):
                return _fail(
                    SS_INPUT_MALFORMED,
                    f"{task_id}: {label} is not a 64-hex digest",
                )

    if [e[0] for e in aggregate.entries] != sorted(e[0] for e in aggregate.entries):
        return _fail(
            SS_INPUT_MALFORMED,
            "entries are not in ascending task_id order; the canonical ordering "
            "is part of the contract",
        )

    if expected_side is not None and aggregate.side != expected_side:
        return _fail(
            SS_SIDE_MISMATCH,
            f"aggregate side {aggregate.side!r} != expected {expected_side!r}",
        )
    if (
        expected_candidate_manifest_digest is not None
        and aggregate.candidate_manifest_digest != expected_candidate_manifest_digest
    ):
        return _fail(
            SS_MANIFEST_MISMATCH,
            "aggregate is bound to candidate manifest "
            f"{aggregate.candidate_manifest_digest[:12]}…, expected "
            f"{expected_candidate_manifest_digest[:12]}…",
        )
    if expected_task_ids is not None:
        expected = set(str(t) for t in expected_task_ids)
        actual = set(seen)
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing or unexpected:
            return _fail(
                SS_TASK_SET_MISMATCH,
                f"task set differs from the candidate manifest: missing={missing} "
                f"unexpected={unexpected}",
            )

    if not aggregate.aggregate_digest:
        return _fail(SS_INPUT_MALFORMED, "aggregate_digest is not bound")
    try:
        computed = aggregate.compute_digest()
    except StateSetError as exc:
        # The payload leaves the RFC-8785-equivalent subset (e.g. a non-ASCII
        # task_id that survived the ordering check).  A verifier must fail
        # closed with a NAMED verdict, never propagate an exception.
        return _fail(SS_NONCANONICAL_INPUT, str(exc))
    if computed != aggregate.aggregate_digest:
        return _fail(
            SS_AGGREGATE_MISMATCH,
            f"recorded digest {aggregate.aggregate_digest[:12]}… != computed "
            f"{computed[:12]}…; the payload was altered after binding",
        )
    return StateSetVerification(
        SS_VERIFIED,
        f"{aggregate.task_count} verified {aggregate.side}-side records bound to "
        f"manifest {aggregate.candidate_manifest_digest[:12]}…",
    )


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m qwen_train.f2_s8_state_set",
        description="Derive the run-level base/gold state-set aggregates.",
    )
    parser.add_argument("--derivation", required=True,
                        help="the f2-s8-state-derivation/v1 document")
    parser.add_argument("--candidate-manifest-digest", default="",
                        help="sha256 of the build manifest bytes (default: the "
                             "digest recorded inside the derivation document)")
    parser.add_argument("--out", default="",
                        help="write the aggregate JSON here (default: stdout)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    doc = json.loads(Path(args.derivation).read_text(encoding="utf-8"))
    manifest_digest = args.candidate_manifest_digest or str(
        doc.get("candidate_manifest_digest") or ""
    )
    payload: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    for side in (SIDE_BASE, SIDE_GOLD):
        agg = build_state_set_aggregate(
            derivation=doc,
            candidate_manifest_digest=manifest_digest,
            side=side,
        )
        payload[side] = agg.to_dict()

    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote state-set aggregates -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
