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
from dataclasses import dataclass
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

#: Every code this module can emit. Machine-checkable, so a typo cannot invent
#: a new refusal class silently.
CODES = frozenset({
    INVALID_GRANT, WRONG_SCHEMA, MALFORMED_TIMESTAMP, NOT_YET_VALID, EXPIRED,
    MISSING_ARTIFACT, PROVENANCE_INVALID, REGISTRATION_UNREADABLE,
    REGISTRATION_BINDING_MISSING, REGISTRATION_BYTES_CHANGED,
    PROVENANCE_DIGEST_MISMATCH, POOL_BYTES_CHANGED, POOL_BINDING_MISMATCH,
    POOL_INVALID, POOL_NOT_IN_REGISTRATION, EMPTY_MANIFEST, DUPLICATE_TASK,
    EXCLUDED_TASK, OUT_OF_POOL, LEDGER_INVALID, CREDITED_TASK, TERMINAL_TASK,
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
    record: Mapping[str, Any], registration_file: Path
) -> _Registration:
    """Read registration and provenance ONCE each; verify the first two links.

    One ``read_bytes`` per file. The digest, the parse and every comparison use
    those exact bytes -- there is no hash-one-read/parse-another window.
    """
    _refuse(
        registration_file.is_file(),
        MISSING_ARTIFACT,
        f"registration file missing: {registration_file}",
    )
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
    now: _dt.datetime | None = None,
) -> VerifiedV2Preflight:
    """Refuse unless every v2 condition holds. Returns frozen evidence. No side effects.

    Refusal order is the documented precedence: grant, registration, pool,
    cross-artifact binding, manifest, ledger.
    """
    current = now or _dt.datetime.now(_dt.timezone.utc)
    schema, not_before, not_after = _check_grant(record, current)

    reg = _read_registration(record, Path(registration_file))
    pool = _read_pool(record, Path(candidate_pool_file))
    _check_registration_anchor(reg, pool)

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
        pool_task_ids=tuple(pool["task_ids"]),
        excluded_task_ids=tuple(sorted(e["task_id"] for e in pool.get("excluded") or [])),
        eligibility=eligibility,
        not_before=not_before,
        not_after=not_after,
    )
