"""Side-effect-free v2 preflight for the S8 live runner (Slice A gate).

WHAT THIS IS
------------
One function, :func:`v2_preflight`, that refuses a v2 execution unless every
input is verified from REAL bytes. It creates no file, writes no budget root,
consumes no authorization, and executes no task. It returns a frozen
:class:`VerifiedV2Preflight`; the runner must use THAT object rather than
reopening the artifacts.

READ-ONCE
---------
The registration file and the candidate pool are each read to bytes exactly
once. The digests, the JSON parse and every comparison are computed from those
same bytes, so a file swapped between "hash" and "parse" cannot produce a
verified object. Nothing downstream may reopen them.

TRUST CHAIN FOR THE REGISTRATION (why it is not a single hash)
--------------------------------------------------------------
The signed grant binds ``registration_sha256``. That value is the
derivation-time **content** digest produced by
``qwen_train/f2_task_plan.registration_artifacts`` (``content_sha256``). It
covers the ordered IDs, the full ordered metadata rows, the excluded rows, the
pinned input digests and ``top_n`` -- so it is NOT recomputable from the
registration file alone.

The checkable chain is therefore three links:

    grant.registration_sha256          == provenance.registration_content_sha256
    provenance.tasks_sha256            == sha256(raw registration file bytes)
    pool.provenance.registration_file_sha256 == sha256(raw registration file bytes)

The first two are what ``qwen_train/f2_s8_precheck._declared_registration_file_digest``
and ``_check_registration_file_digest`` already implement. The THIRD is what
makes the chain tamper-evident: the pool's bytes are themselves bound by the
signed ``candidate_pool_digest``, so an actor who rewrites the registration AND
its adjacent provenance must also rewrite the pool -- which changes the pool
bytes and is refused as ``POOL_BYTES_CHANGED``. Without that third link a
coordinated registration+provenance rewrite left ``registration_content_sha256``
untouched and passed (see ``tests/test_f2_s8_v2_gate.py``).

CONSEQUENCE FOR POOL CONSTRUCTION: every pool must carry
``provenance.registration_file_sha256``. A pool without it is refused
(``REGISTRATION_BINDING_MISSING``). No production pool exists, so this adds a
construction requirement, not a migration.

RESIDUAL LIMIT (documented, not claimed closed): the chain proves the
registration bytes are the ones the signed pool ratified. It does not
independently re-derive ``registration_content_sha256`` from the population and
pinned inputs; doing that needs the union report and acquired-artifact paths,
which the grant does not carry. Recomputing from sibling artifacts would be
worthless (same mutable directory, no trust anchor). Closing THAT would need a
new signed locator -- ``REQUIRES AUTHORIZATION``.

GRANT LOADING
-------------
The gate does not load the grant. The caller obtains the record from
``f2_authorization.require_authorized(...)`` / ``verify_authorization(...)`` --
the single authoritative loader, which verifies signature, trust root, signer,
key epoch, revocation, window, stage, operations and required bindings -- and
passes the resulting record here. There is no second loader.

ELIGIBILITY
-----------
Reused, not redefined: ``qwen_train.f2_s8_factory.AppendOnlyLedger`` supplies
``admitted`` (credited) and ``terminal_scientific``; the same definitions
``select_candidates`` and ``run_once`` use. A recorded-but-not-terminal attempt
is NOT refused here: retry governance belongs to the factory, and refusing it
would block a legitimate retry.

REFUSAL CODES
-------------
Stable, machine-readable ``V2GateError.code`` values. Messages stay for humans;
tests assert codes.

===========================  ==================================================
code                         condition
===========================  ==================================================
INVALID_GRANT                record is not a mapping
WRONG_SCHEMA                 signed schema is not ``f2-authorization/v2``
MALFORMED_TIMESTAMP          validity window missing/unparseable
NOT_YET_VALID                now precedes ``not_before``
EXPIRED                      now exceeds ``not_after``
MISSING_ARTIFACT             registration / provenance / pool file absent, or the
                             grant binds no usable digest
PROVENANCE_INVALID           provenance unreadable, not an object, or declares
                             no usable tasks_sha256 / content digest
REGISTRATION_UNREADABLE      registration bytes are not the authoritative ID list
REGISTRATION_BINDING_MISSING pool provenance carries no registration byte digest
REGISTRATION_BYTES_CHANGED   raw registration bytes disagree with the digest the
                             provenance record or the signed pool declares
PROVENANCE_DIGEST_MISMATCH   grant registration_sha256 != provenance content digest
POOL_BYTES_CHANGED           pool file digest != the signed candidate_pool_digest,
                             or the file is not in canonical form
POOL_BINDING_MISMATCH        pool.registration_sha256 != grant.registration_sha256
POOL_INVALID                 an ID appears in both ``task_ids`` and ``excluded``
POOL_NOT_IN_REGISTRATION     a pool task ID is absent from the registration
EMPTY_MANIFEST               the manifest declares no task
DUPLICATE_TASK               the manifest repeats a task ID
EXCLUDED_TASK                the manifest names an owner-excluded task
OUT_OF_POOL                  the manifest names a task outside the signed pool
LEDGER_INVALID               no ledger supplied, or its hash chain does not verify
CREDITED_TASK                the task already holds an admitted scientific record
TERMINAL_TASK                the task already holds a non-admitted terminal
                             scientific record
===========================  ==================================================

PRECEDENCE (deterministic, in this exact order)
-----------------------------------------------
1. grant:     INVALID_GRANT, WRONG_SCHEMA, MALFORMED_TIMESTAMP, NOT_YET_VALID,
              EXPIRED
2. registration: MISSING_ARTIFACT, PROVENANCE_INVALID, REGISTRATION_UNREADABLE,
              REGISTRATION_BYTES_CHANGED (vs provenance), PROVENANCE_DIGEST_MISMATCH
3. pool:      POOL_BYTES_CHANGED, POOL_BINDING_MISMATCH, POOL_INVALID
4. cross-artifact: REGISTRATION_BINDING_MISSING, REGISTRATION_BYTES_CHANGED
              (vs pool), POOL_NOT_IN_REGISTRATION
5. manifest:  EMPTY_MANIFEST, DUPLICATE_TASK, EXCLUDED_TASK, OUT_OF_POOL
6. ledger:    LEDGER_INVALID, CREDITED_TASK, TERMINAL_TASK

Two consequences are deliberate and tested:

* ``EXCLUDED_TASK`` outranks ``OUT_OF_POOL``. An exclusion is an owner-declared
  prohibition, and it is normally also outside the pool (``build_pool`` records
  excluded IDs separately from ``task_ids``), so letting pool membership decide
  would mask every exclusion behind the weaker reason.
* ``CREDITED_TASK`` outranks ``TERMINAL_TASK``. A credited task also holds a
  terminal record; the irreversible "already counted" outcome is the more
  precise reason.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from qwen_train.f2_authorization import AuthorizationError, _parse_ts
from qwen_train.f2_s8_candidate_pool import (
    PoolError,
    validate_manifest_membership,
    verify_pool_file,
)

V2_SCHEMA = "f2-authorization/v2"

INVALID_GRANT = "INVALID_GRANT"
WRONG_SCHEMA = "WRONG_SCHEMA"
MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
NOT_YET_VALID = "NOT_YET_VALID"
EXPIRED = "EXPIRED"
MISSING_ARTIFACT = "MISSING_ARTIFACT"
PROVENANCE_INVALID = "PROVENANCE_INVALID"
REGISTRATION_UNREADABLE = "REGISTRATION_UNREADABLE"
REGISTRATION_BINDING_MISSING = "REGISTRATION_BINDING_MISSING"
REGISTRATION_BYTES_CHANGED = "REGISTRATION_BYTES_CHANGED"
PROVENANCE_DIGEST_MISMATCH = "PROVENANCE_DIGEST_MISMATCH"
POOL_BYTES_CHANGED = "POOL_BYTES_CHANGED"
POOL_BINDING_MISMATCH = "POOL_BINDING_MISMATCH"
POOL_INVALID = "POOL_INVALID"
POOL_NOT_IN_REGISTRATION = "POOL_NOT_IN_REGISTRATION"
EMPTY_MANIFEST = "EMPTY_MANIFEST"
DUPLICATE_TASK = "DUPLICATE_TASK"
EXCLUDED_TASK = "EXCLUDED_TASK"
OUT_OF_POOL = "OUT_OF_POOL"
LEDGER_INVALID = "LEDGER_INVALID"
CREDITED_TASK = "CREDITED_TASK"
TERMINAL_TASK = "TERMINAL_TASK"

PROBE_OPERATION = "build_probe"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
PROBE_NOT_AUTHORIZED = "PROBE_NOT_AUTHORIZED"
PROBE_IDENTITY_MISMATCH = "PROBE_IDENTITY_MISMATCH"
PROBE_POOL_DIGEST_MISMATCH = "PROBE_POOL_DIGEST_MISMATCH"
PROBE_SELECTION_DIGEST_MISMATCH = "PROBE_SELECTION_DIGEST_MISMATCH"
PROBE_LEDGER_MUTATION_FORBIDDEN = "PROBE_LEDGER_MUTATION_FORBIDDEN"
PROBE_CREDITS_FORBIDDEN = "PROBE_CREDITS_FORBIDDEN"
PROBE_NETWORK_ENFORCEMENT_UNAVAILABLE = "PROBE_NETWORK_ENFORCEMENT_UNAVAILABLE"
PROBE_DISK_LOW = "PROBE_DISK_LOW"
PROBE_REVOKED = "PROBE_REVOKED"
PROBE_TASK_OUTSIDE_SELECTION = "PROBE_TASK_OUTSIDE_SELECTION"
PROBE_PREREQUISITES_MISSING = "PROBE_PREREQUISITES_MISSING"

PROBE_CODES = (
    PROBE_NOT_AUTHORIZED,
    PROBE_IDENTITY_MISMATCH,
    PROBE_POOL_DIGEST_MISMATCH,
    PROBE_SELECTION_DIGEST_MISMATCH,
    PROBE_LEDGER_MUTATION_FORBIDDEN,
    PROBE_CREDITS_FORBIDDEN,
    PROBE_NETWORK_ENFORCEMENT_UNAVAILABLE,
    PROBE_DISK_LOW,
    PROBE_REVOKED,
    PROBE_TASK_OUTSIDE_SELECTION,
    PROBE_PREREQUISITES_MISSING,
)

#: Every code this module can emit. Machine-checkable, so a typo cannot invent
#: a new refusal class silently.
CODES = frozenset({
    INVALID_GRANT, WRONG_SCHEMA, MALFORMED_TIMESTAMP, NOT_YET_VALID, EXPIRED,
    MISSING_ARTIFACT, PROVENANCE_INVALID, REGISTRATION_UNREADABLE,
    REGISTRATION_BINDING_MISSING, REGISTRATION_BYTES_CHANGED,
    PROVENANCE_DIGEST_MISMATCH, POOL_BYTES_CHANGED, POOL_BINDING_MISMATCH,
    POOL_INVALID, POOL_NOT_IN_REGISTRATION, EMPTY_MANIFEST, DUPLICATE_TASK,
    EXCLUDED_TASK, OUT_OF_POOL, LEDGER_INVALID, CREDITED_TASK, TERMINAL_TASK,
    *PROBE_CODES,
})

#: The precedence order above, as data, so a test can pin it.
PRECEDENCE = (
    INVALID_GRANT, WRONG_SCHEMA, MALFORMED_TIMESTAMP, NOT_YET_VALID, EXPIRED,
    MISSING_ARTIFACT, PROVENANCE_INVALID, REGISTRATION_UNREADABLE,
    REGISTRATION_BYTES_CHANGED, PROVENANCE_DIGEST_MISMATCH,
    POOL_BYTES_CHANGED, POOL_BINDING_MISMATCH, POOL_INVALID,
    REGISTRATION_BINDING_MISSING, POOL_NOT_IN_REGISTRATION,
    EMPTY_MANIFEST, DUPLICATE_TASK, EXCLUDED_TASK, OUT_OF_POOL,
    LEDGER_INVALID, CREDITED_TASK, TERMINAL_TASK,
    *PROBE_CODES,
)


class V2GateError(AuthorizationError):
    """The v2 inputs do not satisfy the preflight contract.

    ``code`` is the stable refusal identity; ``str(exc)`` carries ``[CODE]`` plus
    a human-readable diagnostic, so a log line is greppable either way.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in CODES:
            raise ValueError(f"unknown V2GateError code: {code!r}")
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def _refuse(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise V2GateError(code, message)


@dataclass(frozen=True)
class VerifiedV2Preflight:
    """Immutable evidence for one verified v2 preflight.

    Every nested collection is frozen: tuples for sequences and a read-only
    mapping for eligibility, so the runner cannot be handed a mutable object
    that a later edit could rewrite.
    """

    grant_id: str
    schema: str
    registration_sha256: str
    registration_file_sha256: str
    registration_bytes: int
    candidate_pool_digest: str
    manifest_digest: str
    task_ids: tuple[str, ...]
    registration_task_ids: tuple[str, ...]
    pool_task_ids: tuple[str, ...]
    excluded_task_ids: tuple[str, ...]
    eligibility: Mapping[str, str]
    not_before: str
    not_after: str

    def as_evidence(self) -> dict[str, Any]:
        """Plain-dict view for a report. Returns copies, never live state."""
        return {
            "grant_id": self.grant_id,
            "schema": self.schema,
            "registration_sha256": self.registration_sha256,
            "registration_file_sha256": self.registration_file_sha256,
            "registration_bytes": self.registration_bytes,
            "candidate_pool_digest": self.candidate_pool_digest,
            "manifest_digest": self.manifest_digest,
            "task_ids": list(self.task_ids),
            "registration_task_ids": list(self.registration_task_ids),
            "pool_task_ids": list(self.pool_task_ids),
            "excluded_task_ids": list(self.excluded_task_ids),
            "eligibility": dict(self.eligibility),
            "not_before": self.not_before,
            "not_after": self.not_after,
        }


@dataclass(frozen=True)
class _Registration:
    """One read of the registration and its provenance, nothing reopened."""

    file_sha256: str
    content_sha256: str
    byte_count: int
    task_ids: tuple[str, ...]


def _check_grant(
    record: Mapping[str, Any], now: _dt.datetime
) -> tuple[str, str, str]:
    """Schema and validity window. Returns (schema, not_before, not_after)."""
    _refuse(isinstance(record, Mapping), INVALID_GRANT, "grant record is not a mapping")

    schema = record.get("schema")
    _refuse(
        schema == V2_SCHEMA,
        WRONG_SCHEMA,
        f"grant schema is {schema!r}, expected {V2_SCHEMA}",
    )

    try:
        not_before = _parse_ts(record["not_before"])
        not_after = _parse_ts(record["not_after"])
    except (KeyError, TypeError, ValueError, AuthorizationError) as exc:
        raise V2GateError(
            MALFORMED_TIMESTAMP, f"grant validity window is missing or malformed: {exc}"
        ) from exc

    _refuse(
        now >= not_before,
        NOT_YET_VALID,
        f"grant is not yet valid (not_before={record['not_before']})",
    )
    _refuse(
        now <= not_after,
        EXPIRED,
        f"grant expired (not_after={record['not_after']})",
    )
    return str(schema), str(record["not_before"]), str(record["not_after"])


def _read_registration(
    record: Mapping[str, Any],
    registration_file: Path,
    *,
    data: bytes | None = None,
) -> _Registration:
    """Hash registration and provenance ONCE each; verify the first two links.

    When the runner already read the registration bytes for the grant binding,
    those exact bytes are passed in so the file is not reopened. The digest, the
    parse and every comparison use those exact bytes -- no hash-one-read/
    parse-another window.
    """
    _refuse(
        registration_file.is_file(),
        MISSING_ARTIFACT,
        f"registration file missing: {registration_file}",
    )
    if data is None:
        data = registration_file.read_bytes()  # THE single read
    raw = hashlib.sha256(data).hexdigest()

    prov_path = registration_file.with_name(
        registration_file.stem + ".provenance.json"
    )
    _refuse(
        prov_path.is_file(),
        MISSING_ARTIFACT,
        f"registration provenance missing: {prov_path.name}",
    )
    prov_bytes = prov_path.read_bytes()  # THE single read
    try:
        prov = json.loads(prov_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise V2GateError(
            PROVENANCE_INVALID, f"registration provenance unreadable: {exc}"
        ) from exc
    _refuse(
        isinstance(prov, Mapping),
        PROVENANCE_INVALID,
        "registration provenance is not an object",
    )

    declared = prov.get("tasks_sha256")
    _refuse(
        isinstance(declared, str) and len(declared) == 64,
        PROVENANCE_INVALID,
        f"provenance declares no usable tasks_sha256: {declared!r}",
    )
    _refuse(
        declared == raw,
        REGISTRATION_BYTES_CHANGED,
        "registration bytes do not match the provenance tasks_sha256: "
        f"file={raw} declared={declared}",
    )

    content = str(prov.get("registration_content_sha256") or "")
    _refuse(
        bool(content),
        PROVENANCE_INVALID,
        "provenance declares no registration_content_sha256",
    )

    # Same parser the precheck uses, fed the bytes we already hashed.
    from qwen_train.f2_s8_precheck import PrecheckError, _registered_task_ids

    try:
        task_ids = _registered_task_ids(registration_file, raw=data)
    except PrecheckError as exc:
        raise V2GateError(REGISTRATION_UNREADABLE, str(exc)) from exc

    # Second link: the signed grant binds the derivation CONTENT digest, which
    # the provenance record is the only on-disk declaration of.
    grant_digest = record.get("registration_sha256")
    _refuse(
        isinstance(grant_digest, str) and len(grant_digest) == 64,
        MISSING_ARTIFACT,
        f"grant does not bind a 64-hex registration_sha256: {grant_digest!r}",
    )
    _refuse(
        grant_digest == content,
        PROVENANCE_DIGEST_MISMATCH,
        "grant registration_sha256 does not match the provenance content digest: "
        f"grant={grant_digest} provenance={content}",
    )
    return _Registration(
        file_sha256=raw,
        content_sha256=content,
        byte_count=len(data),
        task_ids=tuple(task_ids),
    )


def _read_pool(record: Mapping[str, Any], pool_file: Path) -> dict[str, Any]:
    """Hash the REAL pool bytes, then compare against the signed expectation."""
    bound = record.get("candidate_pool_digest")
    _refuse(
        isinstance(bound, str) and len(bound) == 64,
        MISSING_ARTIFACT,
        "grant does not bind a 64-hex candidate_pool_digest",
    )
    _refuse(pool_file.is_file(), MISSING_ARTIFACT, f"candidate pool missing: {pool_file}")
    try:
        # verify_pool_file hashes the real bytes and parses THAT read only.
        pool = verify_pool_file(
            pool_file,
            expected_digest=bound,
            expected_registration_sha256=str(record.get("registration_sha256")),
        )
    except PoolError as exc:
        message = str(exc)
        if "different registration digest" in message:
            code = POOL_BINDING_MISMATCH
        elif "digest mismatch" in message or "not in canonical form" in message:
            code = POOL_BYTES_CHANGED
        else:
            code = POOL_INVALID
        raise V2GateError(code, f"candidate pool rejected: {message}") from exc

    overlap = set(pool["task_ids"]) & {e["task_id"] for e in pool.get("excluded") or []}
    _refuse(
        not overlap,
        POOL_INVALID,
        f"pool IDs appear in both task_ids and excluded: {sorted(overlap)[:5]}",
    )
    return pool


def _check_registration_anchor(
    reg: _Registration, pool: Mapping[str, Any]
) -> None:
    """Third link: the SIGNED pool must bind the registration's raw bytes.

    The pool's bytes are covered by ``candidate_pool_digest`` in the signed
    grant, so this value cannot be altered without changing the pool and being
    refused as ``POOL_BYTES_CHANGED``. This is what closes the
    registration+provenance coordinated-rewrite gap.
    """
    provenance = pool.get("provenance")
    anchor = (
        provenance.get("registration_file_sha256")
        if isinstance(provenance, Mapping)
        else None
    )
    _refuse(
        isinstance(anchor, str) and len(anchor) == 64,
        REGISTRATION_BINDING_MISSING,
        "candidate pool provenance carries no registration_file_sha256; the "
        "signed pool cannot then prove which registration bytes it ratified",
    )
    _refuse(
        anchor == reg.file_sha256,
        REGISTRATION_BYTES_CHANGED,
        "registration bytes do not match the digest the signed candidate pool "
        f"bound: file={reg.file_sha256} pool={anchor}",
    )


def _check_manifest(
    manifest_task_ids: Iterable[str], pool: Mapping[str, Any]
) -> tuple[str, ...]:
    """Ordered refusal: empty, duplicate, excluded, out-of-pool."""
    manifest = [str(t) for t in manifest_task_ids]
    _refuse(bool(manifest), EMPTY_MANIFEST, "manifest carries no task ids")

    duplicates = sorted({t for t in manifest if manifest.count(t) > 1})
    _refuse(
        not duplicates,
        DUPLICATE_TASK,
        f"manifest contains duplicate task ids: {duplicates[:5]}",
    )

    excluded = {e["task_id"] for e in pool.get("excluded") or []}
    blocked = sorted(set(manifest) & excluded)
    _refuse(
        not blocked,
        EXCLUDED_TASK,
        f"manifest contains excluded task(s): {blocked[:5]}",
    )

    try:
        checked = validate_manifest_membership(manifest, pool)
    except PoolError as exc:
        raise V2GateError(OUT_OF_POOL, f"manifest rejected: {exc}") from exc

    missing = sorted(set(checked) - set(pool["task_ids"]))
    _refuse(not missing, OUT_OF_POOL, f"manifest task(s) outside the pool: {missing[:5]}")
    return tuple(checked)


def _check_eligibility(
    task_ids: Sequence[str], ledger_paths: Sequence[Path]
) -> Mapping[str, str]:
    """Reuse the authoritative credited/terminal definitions. Read-only."""
    _refuse(
        bool(ledger_paths),
        LEDGER_INVALID,
        "no ledger supplied: cannot establish eligibility",
    )
    from qwen_train.f2_s8_factory import AppendOnlyLedger, LedgerError

    ledgers: list[AppendOnlyLedger] = []
    for path in ledger_paths:
        ledger = AppendOnlyLedger(path)
        try:
            ok, reason = ledger.verify()
        except LedgerError as exc:
            raise V2GateError(LEDGER_INVALID, f"ledger {path} is invalid: {exc}") from exc
        _refuse(
            ok,
            LEDGER_INVALID,
            f"ledger {path} failed verification: {reason}",
        )
        ledgers.append(ledger)

    eligibility: dict[str, str] = {}
    for task_id in task_ids:
        try:
            credited = any(ledger.admitted(task_id) for ledger in ledgers)
            terminal = any(ledger.terminal_scientific(task_id) for ledger in ledgers)
        except LedgerError as exc:
            raise V2GateError(LEDGER_INVALID, f"ledger is invalid: {exc}") from exc
        _refuse(
            not credited,
            CREDITED_TASK,
            f"task {task_id!r} already holds an admitted scientific record",
        )
        _refuse(
            not terminal,
            TERMINAL_TASK,
            f"task {task_id!r} already holds a terminal scientific record",
        )
        eligibility[task_id] = "clear"
    return MappingProxyType(eligibility)


def v2_preflight(
    *,
    record: Mapping[str, Any],
    registration_file: Path | str,
    candidate_pool_file: Path | str,
    manifest_task_ids: Iterable[str],
    ledger_paths: Sequence[Path | str] = (),
    manifest_digest: str = "",
    registration_bytes: bytes | None = None,
    now: _dt.datetime | None = None,
    exclusion_policy_file: Path | str | None = None,
) -> VerifiedV2Preflight:
    """Refuse unless every v2 condition holds. Returns frozen evidence. No side effects.

    Refusal order is the documented precedence: grant, registration, pool,
    cross-artifact binding, manifest, ledger.
    """
    current = now or _dt.datetime.now(_dt.timezone.utc)
    schema, not_before, not_after = _check_grant(record, current)

    reg = _read_registration(
        record, Path(registration_file), data=registration_bytes
    )
    pool = _read_pool(record, Path(candidate_pool_file))
    _check_registration_anchor(reg, pool)

    if exclusion_policy_file is not None:
        from qwen_train.f2_s8_candidate_pool import load_exclusion_policy

        try:
            _pol, forbidden_ids, _pol_sha = load_exclusion_policy(exclusion_policy_file)
        except PoolError as exc:
            raise V2GateError(POOL_INVALID, f"exclusion policy invalid: {exc}") from exc
        leaked_in_pool = sorted(set(pool["task_ids"]) & forbidden_ids)
        _refuse(
            not leaked_in_pool,
            EXCLUDED_TASK,
            f"candidate pool includes EXCLUDE-185 task(s): {leaked_in_pool[:5]}",
        )
        leaked_in_manifest = sorted({str(t) for t in manifest_task_ids} & forbidden_ids)
        _refuse(
            not leaked_in_manifest,
            EXCLUDED_TASK,
            f"manifest includes EXCLUDE-185 task(s): {leaked_in_manifest[:5]}",
        )

    not_registered = sorted(set(pool["task_ids"]) - set(reg.task_ids))
    _refuse(
        not not_registered,
        POOL_NOT_IN_REGISTRATION,
        "pool task(s) are absent from the registration the grant binds: "
        f"{not_registered[:5]}",
    )

    checked = _check_manifest(manifest_task_ids, pool)
    eligibility = _check_eligibility(checked, [Path(p) for p in ledger_paths])

    return VerifiedV2Preflight(
        grant_id=str(record.get("id") or ""),
        schema=schema,
        registration_sha256=reg.content_sha256,
        registration_file_sha256=reg.file_sha256,
        registration_bytes=reg.byte_count,
        candidate_pool_digest=str(record["candidate_pool_digest"]),
        manifest_digest=str(manifest_digest or ""),
        task_ids=checked,
        registration_task_ids=reg.task_ids,
        pool_task_ids=tuple(pool["task_ids"]),
        excluded_task_ids=tuple(sorted(e["task_id"] for e in pool.get("excluded") or [])),
        eligibility=eligibility,
        not_before=not_before,
        not_after=not_after,
    )


@dataclass(frozen=True)
class S8ProbeAuthorizedRun:
    """Immutable evidence for one verified v2 build-probe authorized run."""

    grant_id: str
    grant_digest: str
    stage: str
    operation: str
    repository_revision: str
    probe_pool_digest: str
    probe_selection_digest: str
    ordering_policy: str
    allowed_identities: tuple[str, ...]
    network_policy: str
    retention_policy: str
    selected_task_ids: tuple[str, ...]
    ledger_fingerprints_before: Mapping[str, str]
    budget_limits: Mapping[str, int]
    batch_limit: int
    disk_floor_bytes: int
    per_task_disk_bytes: int
    target_compatible: int = 0
    execution_id: str | None = None
    not_before: str = ""
    not_after: str = ""
    record: Mapping[str, Any] = field(default_factory=dict)
    #: Retained authoritative sources, used to RE-DERIVE the authorization from
    #: disk on every mid-run revalidation.  An in-memory snapshot is never the
    #: sole authority for mutable disk state.
    grant_path: str = ""
    registration_file: str = ""
    registration_manifest_file: str = ""
    compat_report_file: str = ""
    exclusion_policy_file: str = ""
    exclusion_policy_sha256: str = ""
    compat_report_sha256: str = ""
    ledger_paths: tuple[str, ...] = ()
    built_task_ids: tuple[str, ...] = ()
    trust_root_path: str = ""
    repository_root: str = ""
    budget_ledger_root: str = ""
    current_username: str = ""


SUPPORTED_COMPAT_SCHEMAS = frozenset({
    "f2-plugin-compat/v1",
    "f2-s8-plugin-compat/v1",
    "f2-plugin-compat-v2",
})


def _compat_classes_from_report(data: Mapping[str, Any]) -> dict[str, str]:
    """Read and strictly validate the compat report's task classes."""
    if not isinstance(data, Mapping):
        raise V2GateError(PROBE_NOT_AUTHORIZED, "compat report is not a JSON object")

    schema = data.get("schema") or data.get("rule_version")
    if not schema or str(schema) not in SUPPORTED_COMPAT_SCHEMAS:
        raise V2GateError(
            PROBE_NOT_AUTHORIZED,
            f"unsupported or missing compat report schema: {schema!r}; "
            f"expected one of {sorted(SUPPORTED_COMPAT_SCHEMAS)}",
        )

    has_tasks = "tasks" in data and isinstance(data["tasks"], Mapping)
    has_results = "results" in data and isinstance(data["results"], list)
    has_unknown = "unknown_tasks" in data and isinstance(data["unknown_tasks"], list)

    tasks_classes: dict[str, str] = {}
    if has_tasks:
        for tid, row in data["tasks"].items():
            if isinstance(row, Mapping) and "classification" in row:
                tasks_classes[str(tid)] = str(row["classification"] or "")
            else:
                raise V2GateError(
                    PROBE_NOT_AUTHORIZED, f"malformed tasks entry for {tid!r}"
                )

    results_classes: dict[str, str] = {}
    if has_results:
        for row in data["results"]:
            if isinstance(row, Mapping) and row.get("task_id"):
                results_classes[str(row["task_id"])] = str(row.get("classification") or "")
            else:
                raise V2GateError(
                    PROBE_NOT_AUTHORIZED, f"malformed results row in compat report: {row!r}"
                )

    if has_tasks and has_results:
        common_keys = set(tasks_classes.keys()) & set(results_classes.keys())
        conflicts = [k for k in common_keys if tasks_classes[k] != results_classes[k]]
        if conflicts:
            raise V2GateError(
                PROBE_NOT_AUTHORIZED,
                f"compat report has conflicting classifications between tasks and results for: {conflicts[:5]}",
            )
        return dict(tasks_classes) if tasks_classes else dict(results_classes)

    if has_tasks:
        if not tasks_classes:
            raise V2GateError(PROBE_NOT_AUTHORIZED, "compat report tasks mapping is empty")
        return tasks_classes

    if has_results:
        if not results_classes:
            raise V2GateError(PROBE_NOT_AUTHORIZED, "compat report results list is empty")
        return results_classes

    if has_unknown:
        classes: dict[str, str] = {}
        for row in data["unknown_tasks"]:
            tid = row.get("task_id") if isinstance(row, Mapping) else row
            if isinstance(tid, str) and tid:
                classes[tid] = "UNKNOWN"
            else:
                raise V2GateError(PROBE_NOT_AUTHORIZED, f"malformed unknown_tasks entry: {row!r}")
        if classes:
            return classes

    raise V2GateError(
        PROBE_NOT_AUTHORIZED,
        "compat report has no usable tasks/results/unknown_tasks grouping",
    )


def authorize_s8_probe_run(
    *,
    grant_path: Path | str | None = None,
    record: Mapping[str, Any] | None = None,
    registration_file: Path | str,
    registration_manifest_file: Path | str | None = None,
    compat_report_file: Path | str,
    exclusion_policy_file: Path | str | None = None,
    ledger_paths: Sequence[Path | str] = (),
    built_task_ids: Iterable[str] = (),
    trust_root_path: Path | str | None = None,
    repository_root: Path | str | None = None,
    now: _dt.datetime | None = None,
    budget_ledger_root: Path | str | None = None,
    #: Verification-only by default.  A caller that is about to perform a real
    #: protected execution MUST pass ``reserve_execution=True`` explicitly.
    #: Authorization checks, preflight, dry-runs, revalidation and inspection
    #: must never burn budget as a side effect of verifying a grant.
    reserve_execution: bool = False,
    execution_id: str | None = None,
    current_username: str | None = None,
) -> S8ProbeAuthorizedRun:
    """Verify authorization for a build-probe run. Fail-closed on any discrepancy.

    Verification is non-reserving unless the caller explicitly requests a
    reservation.  Only the real execution path may do so.
    """
    from qwen_train.f2_s8_acquisition import require_unprivileged_identity

    require_unprivileged_identity("authorize_s8_probe_run")

    username = (
        current_username
        if current_username is not None
        else (os.environ.get("USERNAME") or "unknown")
    ).lower()

    from qwen_train.f2_authorization import record_digest, verify_authorization
    from qwen_train.f2_s8_probe_driver import compute_ordered_probe_candidates

    # Read registration bytes and the provenance/content digest.
    reg_path = Path(registration_file)
    if not reg_path.is_file():
        raise V2GateError(MISSING_ARTIFACT, f"registration file missing: {reg_path}")
    reg_bytes = reg_path.read_bytes()
    file_sha256 = hashlib.sha256(reg_bytes).hexdigest()

    # The provenance artifact supplies the semantic content digest; the real
    # registration file bytes supply registration_manifest_sha256.  These are
    # distinct values and must not be substituted for one another.
    if registration_manifest_file is not None:
        prov_path = Path(registration_manifest_file)
    else:
        prov_path = reg_path.with_name(reg_path.stem + ".provenance.json")
        if not prov_path.is_file():
            for candidate in (
                reg_path.parent / "registration_manifest.json",
                Path("qwen_train/curriculum/f2_registration/registration_manifest.json"),
                Path("data/f2_population/registration/registration_manifest.json"),
            ):
                if candidate.is_file():
                    prov_path = candidate
                    break
    if not prov_path.is_file():
        raise V2GateError(
            MISSING_ARTIFACT, f"registration provenance missing: {prov_path}"
        )
    try:
        provenance = json.loads(prov_path.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise V2GateError(
            PROVENANCE_INVALID, f"registration provenance unreadable: {exc}"
        ) from exc
    if not isinstance(provenance, Mapping):
        raise V2GateError(PROVENANCE_INVALID, "registration provenance is not an object")
    content_digest = str(provenance.get("registration_content_sha256") or "")
    if not _SHA256_HEX.fullmatch(content_digest):
        raise V2GateError(
            PROVENANCE_INVALID,
            "provenance declares no 64-hex registration_content_sha256",
        )
    declared_tasks = str(provenance.get("tasks_sha256") or "")
    if not _SHA256_HEX.fullmatch(declared_tasks):
        raise V2GateError(
            PROVENANCE_INVALID,
            "provenance declares no 64-hex tasks_sha256; cannot bind registration bytes",
        )
    if declared_tasks != file_sha256:
        raise V2GateError(
            REGISTRATION_BYTES_CHANGED,
            "registration bytes do not match the provenance tasks_sha256: "
            f"file={file_sha256} declared={declared_tasks}",
        )

    # Read compat report to get UNKNOWN tasks
    compat_path = Path(compat_report_file)
    if not compat_path.is_file():
        raise V2GateError(MISSING_ARTIFACT, f"compat report missing: {compat_path}")
    raw_compat_bytes = compat_path.read_bytes()
    compat_report_sha256 = hashlib.sha256(raw_compat_bytes).hexdigest()
    try:
        compat_data = json.loads(raw_compat_bytes.decode("utf-8"))
    except Exception as exc:
        raise V2GateError(PROBE_NOT_AUTHORIZED, f"unreadable compat report: {exc}") from exc

    compat_classes = _compat_classes_from_report(compat_data)
    # EXCLUDE-185: the compat report records the SOURCE_PRIORITY-resolved
    # cross-source conflicts separately.  They must not enter the probe
    # selection even when their plugin classification is UNKNOWN.
    raw_conflicts = compat_data.get("resolved_by_source_priority") or []
    if not isinstance(raw_conflicts, list):
        raise V2GateError(
            PROBE_NOT_AUTHORIZED,
            "compat report resolved_by_source_priority must be a list",
        )
    conflict_ids = {str(x) for x in raw_conflicts}
    resolved_excl_path = Path(exclusion_policy_file) if exclusion_policy_file else None
    policy_sha = ""
    if resolved_excl_path is not None:
        from qwen_train.f2_s8_candidate_pool import load_exclusion_policy

        try:
            _pol, policy_ids, policy_sha = load_exclusion_policy(resolved_excl_path)
        except PoolError as exc:
            raise V2GateError(
                PROBE_NOT_AUTHORIZED, f"exclusion policy rejected: {exc}"
            ) from exc
        if conflict_ids and conflict_ids != policy_ids:
            raise V2GateError(
                PROBE_NOT_AUTHORIZED,
                "compat report resolved_by_source_priority disagrees with exclusion policy",
            )
        conflict_ids |= policy_ids
    if conflict_ids:
        compat_classes = {
            tid: cls for tid, cls in compat_classes.items() if tid not in conflict_ids
        }

    # Compute registration order.  Two deliberate formats are accepted:
    #   * ordered metadata (a list of objects with ``instance_id``), and
    #   * a plain JSON list of task-id strings.
    # Anything else is a terminal refusal.  Falling back to ``compat_classes``
    # key order would silently change which tasks the probe selects.
    from qwen_train.f2_s8_acquisition import load_registration_order

    try:
        registration_order = load_registration_order(reg_path)
    except Exception:
        try:
            parsed = json.loads(reg_bytes)
        except Exception as exc:
            raise V2GateError(
                REGISTRATION_UNREADABLE,
                f"registration is not parseable JSON: {exc}",
            ) from exc
        if not isinstance(parsed, list) or not parsed:
            raise V2GateError(
                REGISTRATION_UNREADABLE,
                "registration is neither ordered metadata nor a non-empty id list",
            )
        if all(isinstance(x, str) for x in parsed):
            order = [str(x) for x in parsed]
        elif all(isinstance(x, dict) and x.get("instance_id") for x in parsed):
            order = [str(x["instance_id"]) for x in parsed]
        else:
            raise V2GateError(
                REGISTRATION_UNREADABLE,
                "registration entries are neither task-id strings nor instance_id objects",
            )
        if len(set(order)) != len(order):
            raise V2GateError(
                REGISTRATION_UNREADABLE, "registration contains duplicate task ids"
            )
        registration_order = order

    # Terminal tasks from ledgers.  Every configured ledger must verify before
    # its rows may influence the selection: an unverified chain could hide a
    # terminal task or inject one.  A missing configured ledger is a hard
    # refusal so a typo or wrong path never silently alters the selection.
    from qwen_train.f2_s8_factory import AppendOnlyLedger

    term_ids: set[str] = set()
    for lp in ledger_paths:
        p = Path(lp)
        if not p.is_file():
            raise V2GateError(
                MISSING_ARTIFACT,
                f"configured admission ledger missing: {p}",
            )
        ledger = AppendOnlyLedger(p)
        ok, reason = ledger.verify()
        if not ok:
            raise V2GateError(
                PROBE_NOT_AUTHORIZED,
                f"admission ledger failed verification ({p}): {reason}",
            )
        for r in ledger.records():
            if r.get("outcome_class") == "scientific":
                term_ids.add(str(r.get("task_id")))

    # The probe population excludes tasks already terminal/credited in the
    # registered chains; they are not UNKNOWN candidates.
    if term_ids:
        compat_classes = {
            tid: cls for tid, cls in compat_classes.items() if tid not in term_ids
        }

    # Read the grant record
    if grant_path is None:
        raise V2GateError(
            PROBE_NOT_AUTHORIZED,
            "grant_path is required; unverified probe record attestation is forbidden",
        )
    grant_file_path = Path(grant_path)
    if not grant_file_path.is_file():
        raise V2GateError(MISSING_ARTIFACT, f"grant file missing: {grant_file_path}")
    try:
        doc = json.loads(grant_file_path.read_text("utf-8"))
        rec = doc.get("record") if isinstance(doc, dict) and "record" in doc else doc
    except Exception as exc:
        raise V2GateError(INVALID_GRANT, f"unparseable grant: {exc}") from exc
    if not isinstance(rec, Mapping):
        raise V2GateError(INVALID_GRANT, "grant record is not a JSON object")

    ordering_policy = str(rec.get("ordering_policy") or "REGISTRATION_ORDER_UNBUILT")
    raw_budget = rec.get("budget") or {}
    max_total_tasks = int(raw_budget.get("max_total_tasks") or 50)
    target_compatible = rec.get("target_compatible", 0)
    if (
        isinstance(target_compatible, bool)
        or not isinstance(target_compatible, int)
        or target_compatible < 0
    ):
        raise V2GateError(
            PROBE_NOT_AUTHORIZED,
            "probe grant target_compatible must be a non-negative integer",
        )
    if target_compatible > max_total_tasks:
        raise V2GateError(
            PROBE_NOT_AUTHORIZED,
            "probe grant target_compatible exceeds max_total_tasks",
        )

    # Compute expected probe candidates
    computed_pool_digest, selected_ids, computed_selection_digest = (
        compute_ordered_probe_candidates(
            registration_order=registration_order,
            compat_classes=compat_classes,
            terminal_task_ids=term_ids,
            built_task_ids=set(built_task_ids),
            ordering_policy=ordering_policy,
            max_tasks=max_total_tasks,
        )
    )

    # Check pool and selection digests
    grant_pool_digest = rec.get("probe_pool_digest")
    if grant_pool_digest != computed_pool_digest:
        raise V2GateError(
            PROBE_POOL_DIGEST_MISMATCH,
            f"probe_pool_digest mismatch: grant={grant_pool_digest} computed={computed_pool_digest}",
        )
    grant_sel_digest = rec.get("probe_selection_digest")
    if grant_sel_digest != computed_selection_digest:
        raise V2GateError(
            PROBE_SELECTION_DIGEST_MISMATCH,
            f"probe_selection_digest mismatch: grant={grant_sel_digest} computed={computed_selection_digest}",
        )

    # Expected binding for verification
    expected_binding = {
        "repository_revision": rec.get("repository_revision"),
        "registration_sha256": content_digest,
        "registration_manifest_sha256": file_sha256,
        "probe_pool_digest": computed_pool_digest,
        "probe_selection_digest": computed_selection_digest,
        "ordering_policy": ordering_policy,
        "allowed_identities": rec.get("allowed_identities"),
        "network_policy": rec.get("network_policy"),
        "retention_policy": rec.get("retention_policy"),
        "awards_credits": False,
    }

    verified_rec = verify_authorization(
        grant_path,
        stage="S8-001",
        operation="build_probe",
        binding=expected_binding,
        trust_root=trust_root_path,
        repository_root=repository_root,
        now=now,
    )

    # Verify identity
    allowed_ids = tuple(
        str(u).lower() for u in (verified_rec.get("allowed_identities") or ())
    )
    if username and allowed_ids and username not in allowed_ids:
        raise V2GateError(
            PROBE_IDENTITY_MISMATCH,
            f"current user {username!r} not in allowed_identities {allowed_ids!r}",
        )

    # Pre-probe snapshot of admission ledgers
    ledger_fingerprints: dict[str, str] = {}
    for lp in ledger_paths:
        p = Path(lp)
        if p.is_file():
            ledger_fingerprints[str(p.resolve())] = hashlib.sha256(
                p.read_bytes()
            ).hexdigest()

    # Reserve budget if requested
    exec_id = execution_id or f"probe-{hashlib.sha256(str(time.time()).encode()).hexdigest()[:12]}"
    if reserve_execution:
        from qwen_train.f2_authz_budget import reserve

        reserve(
            record=verified_rec,
            binding=expected_binding,
            binding_digest=computed_selection_digest,
            stage="S8-001",
            operation="build_probe",
            execution_id=exec_id,
            task_count=len(selected_ids) or 1,
            task_ids=list(selected_ids) if selected_ids else ["task-0"],
            ledger_root=budget_ledger_root,
        )

    return S8ProbeAuthorizedRun(
        grant_id=str(verified_rec.get("id") or ""),
        grant_digest=record_digest(verified_rec),
        stage=str(verified_rec.get("stage") or "S8-001"),
        operation="build_probe",
        repository_revision=str(verified_rec.get("repository_revision") or ""),
        probe_pool_digest=computed_pool_digest,
        probe_selection_digest=computed_selection_digest,
        ordering_policy=ordering_policy,
        allowed_identities=allowed_ids,
        network_policy=str(
            verified_rec.get("network_policy") or "OFFLINE_WHEELHOUSE_ONLY"
        ),
        retention_policy=str(
            verified_rec.get("retention_policy")
            or "RETAIN_COMPATIBLE_DELETE_FAILED_AND_INCOMPATIBLE"
        ),
        selected_task_ids=tuple(selected_ids),
        ledger_fingerprints_before=MappingProxyType(ledger_fingerprints),
        budget_limits=MappingProxyType(dict(raw_budget)),
        batch_limit=int(raw_budget.get("batch_limit") or 10),
        disk_floor_bytes=int(raw_budget.get("disk_floor_bytes") or 25_000_000_000),
        per_task_disk_bytes=int(
            raw_budget.get("per_task_disk_bytes") or 3_500_000_000
        ),
        target_compatible=target_compatible,
        execution_id=exec_id,
        not_before=str(verified_rec.get("not_before") or ""),
        not_after=str(verified_rec.get("not_after") or ""),
        record=MappingProxyType(dict(verified_rec)),
        grant_path=str(grant_file_path),
        registration_file=str(reg_path),
        registration_manifest_file=str(prov_path),
        compat_report_file=str(compat_path),
        compat_report_sha256=compat_report_sha256,
        exclusion_policy_file=str(resolved_excl_path) if resolved_excl_path else "",
        exclusion_policy_sha256=policy_sha,
        ledger_paths=tuple(str(x) for x in ledger_paths),
        built_task_ids=tuple(sorted({str(x) for x in built_task_ids})),
        trust_root_path=str(trust_root_path) if trust_root_path else "",
        repository_root=str(repository_root) if repository_root else "",
        budget_ledger_root=str(budget_ledger_root) if budget_ledger_root else "",
        current_username=username,
    )


def check_probe_grant_still_valid(
    authorized: S8ProbeAuthorizedRun,
    *,
    now: _dt.datetime | None = None,
    budget_ledger_root: Path | str | None = None,
) -> bool:
    from qwen_train.f2_authorization import _parse_ts

    current = now or _dt.datetime.now(_dt.timezone.utc)
    if authorized.not_after:
        na = _parse_ts(authorized.not_after)
        if current > na:
            raise V2GateError(EXPIRED, f"probe grant expired at {authorized.not_after}")

    if not (
        authorized.grant_path
        and authorized.registration_file
        and authorized.compat_report_file
    ):
        raise V2GateError(
            PROBE_NOT_AUTHORIZED,
            "revalidation requires the retained authoritative source paths "
            "(grant, registration, compat report)",
        )

    # 1. Admission ledgers must be byte-identical to the launch snapshot.
    for p_str, old_sha in authorized.ledger_fingerprints_before.items():
        p = Path(p_str)
        if not p.is_file():
            raise V2GateError(
                PROBE_LEDGER_MUTATION_FORBIDDEN,
                f"admission ledger missing during probe: {p}",
            )
        curr_sha = hashlib.sha256(p.read_bytes()).hexdigest()
        if curr_sha != old_sha:
            raise V2GateError(
                PROBE_LEDGER_MUTATION_FORBIDDEN,
                f"admission ledger mutated during probe: {p} was {old_sha} now {curr_sha}",
            )

    # 2. Explicit trust-root revocation check.  Run BEFORE re-deriving so a
    #    revoked record or a bad pin is reported as such, not as generic drift.
    if authorized.record and authorized.record.get("id"):
        from qwen_train.f2_authorization import load_trust_root

        trust_root = (
            Path(authorized.trust_root_path)
            if authorized.trust_root_path
            else None
        )
        try:
            _signers, _rev_keys, _epochs, rev_records = load_trust_root(
                trust_root, require_pin=True
            )
            if authorized.record["id"] in rev_records:
                raise V2GateError(
                    PROBE_REVOKED,
                    f"probe grant {authorized.record['id']!r} is revoked in trust root",
                )
        except V2GateError:
            raise
        except Exception as exc:
            raise V2GateError(
                PROBE_NOT_AUTHORIZED,
                f"trust root verification failed during probe revalidation: {exc}",
            ) from exc

    # 3. Re-derive the authorization from the authoritative files on disk.
    #    This re-reads the grant and re-verifies its signature, trust root,
    #    revocation, revision/dirty binding, registration and manifest bytes,
    #    compat report, and recomputes the probe selection.  It is verification
    #    only: it must never reserve execution budget.
    try:
        fresh = authorize_s8_probe_run(
            grant_path=authorized.grant_path,
            registration_file=authorized.registration_file,
            registration_manifest_file=(
                authorized.registration_manifest_file or None
            ),
            compat_report_file=authorized.compat_report_file,
            exclusion_policy_file=authorized.exclusion_policy_file or None,
            ledger_paths=authorized.ledger_paths,
            built_task_ids=authorized.built_task_ids,
            trust_root_path=authorized.trust_root_path or None,
            repository_root=authorized.repository_root or None,
            now=current,
            budget_ledger_root=(
                budget_ledger_root or authorized.budget_ledger_root or None
            ),
            reserve_execution=False,
            execution_id=authorized.execution_id,
            current_username=authorized.current_username or None,
        )
    except V2GateError:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure to re-derive is fatal
        raise V2GateError(
            PROBE_NOT_AUTHORIZED,
            f"authoritative revalidation failed: {exc}",
        ) from exc

    # 4. The re-derived authorization must be exactly the same authorization.
    for field_name in (
        "grant_id",
        "grant_digest",
        "stage",
        "operation",
        "repository_revision",
        "probe_pool_digest",
        "probe_selection_digest",
        "ordering_policy",
        "allowed_identities",
        "network_policy",
        "retention_policy",
        "selected_task_ids",
        "target_compatible",
        "not_before",
        "not_after",
        "exclusion_policy_sha256",
        "compat_report_sha256",
    ):
        was = getattr(authorized, field_name)
        now_value = getattr(fresh, field_name)
        if was != now_value:
            raise V2GateError(
                PROBE_NOT_AUTHORIZED,
                f"authoritative revalidation drift in {field_name}: "
                f"was {was!r} now {now_value!r}",
            )

    # 5. Budget store integrity / revocation, using the retained root so an
    #    explicit temporary root is never silently replaced by production.
    from qwen_train.f2_authz_budget import audit, BudgetError

    try:
        audit(
            authorized.grant_digest,
            budget_ledger_root or authorized.budget_ledger_root or None,
            is_probe=True,
        )
    except BudgetError as exc:
        if "REVOKED" in str(exc) or "AUTHORIZATION_REVOKED" in str(exc):
            raise V2GateError(PROBE_REVOKED, f"probe grant revoked: {exc}") from exc
        raise V2GateError(
            PROBE_NOT_AUTHORIZED,
            f"budget store audit failed during probe revalidation: {exc}",
        ) from exc
    except Exception as exc:
        raise V2GateError(
            PROBE_NOT_AUTHORIZED,
            f"unexpected failure during budget audit: {exc}",
        ) from exc
    return True
