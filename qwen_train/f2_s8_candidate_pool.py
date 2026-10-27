"""S8 candidate authorization pool — explicit membership, not a registration digest.

WHY THIS EXISTS
---------------
The registration manifest authorizes *nothing to execute*: it records which task
IDs were derived from the population, not which of them the owner has ratified
for protected execution. A grant that merely binds the registration digest would
therefore authorize three thousand tasks nobody approved.

This module provides the explicit artifact the owner signs over instead:

    {"schema": "f2-s8-candidate-pool/v1",
     "registration_sha256": "<content digest>",
     "task_ids": ["...", ...],          # sorted, duplicate-free, validated
     "excluded": [{"task_id": "...", "reason": "..."}],
     "provenance": {...}}

CANONICALIZATION AND DIGEST
---------------------------
The artifact is serialized deterministically:

    json.dumps(pool, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

with ``task_ids`` sorted and duplicate-free. The digest is SHA-256 over **the
exact bytes written**. A caller that supplies a digest string proves nothing; a
consumer must hash the real file and then confirm the parsed structure re-
serializes to those same bytes, so a reformatted or reordered file is rejected
rather than silently accepted under an older digest.

There is deliberately NO production pool here. Building one requires owner-
approved membership and the disposition of the conflict rows; both are
``REQUIRES AUTHORIZATION``. Tests construct synthetic pools.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any, Iterable, Mapping, Sequence

POOL_SCHEMA = "f2-s8-candidate-pool/v1"
EXCLUSION_POLICY_SCHEMA = "f2-s8-exclusion-policy/v1"

#: Instance IDs are ASCII repository slugs; anything else cannot be a task.
_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class PoolError(ValueError):
    """The pool or the manifest does not satisfy the membership contract."""


def load_exclusion_policy(
    path: pathlib.Path | str,
    *,
    expected_digest: str | None = None,
) -> tuple[dict[str, Any], set[str], str]:
    """Load and validate an EXCLUDE-185 policy artifact.

    Returns ``(policy_dict, excluded_task_ids_set, file_sha256)``.
    Refuses non-canonical bytes, duplicate/unsorted/malformed task IDs, or a
    digest mismatch.
    """
    p = pathlib.Path(path)
    if not p.is_file():
        raise PoolError(f"exclusion policy file missing: {p}")
    raw = p.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        policy = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PoolError(f"exclusion policy is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(policy, Mapping):
        raise PoolError("exclusion policy is not a JSON object")
    if policy.get("schema") != EXCLUSION_POLICY_SCHEMA:
        raise PoolError(
            f"unsupported exclusion policy schema: {policy.get('schema')!r}"
        )
    ids = policy.get("excluded_task_ids")
    if not isinstance(ids, list) or not ids:
        raise PoolError("exclusion policy carries no excluded_task_ids list")
    if ids != sorted(ids):
        raise PoolError("exclusion policy excluded_task_ids are not sorted")
    if len(set(ids)) != len(ids):
        raise PoolError("exclusion policy excluded_task_ids contain duplicates")
    for tid in ids:
        if not isinstance(tid, str) or not _ID_RE.match(tid):
            raise PoolError(f"malformed task id in exclusion policy: {tid!r}")
    declared_count = policy.get("count")
    if declared_count is not None and (
        isinstance(declared_count, bool)
        or not isinstance(declared_count, int)
        or declared_count != len(ids)
    ):
        raise PoolError(
            f"exclusion policy count {declared_count!r} does not match {len(ids)}"
        )
    canonical_list = json.dumps(
        ids, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    computed_list_sha = hashlib.sha256(canonical_list).hexdigest()
    declared_list_sha = policy.get("excluded_task_ids_sha256")
    if declared_list_sha is not None and declared_list_sha != computed_list_sha:
        raise PoolError(
            "exclusion policy excluded_task_ids_sha256 mismatch: "
            f"declared={declared_list_sha} computed={computed_list_sha}"
        )
    if expected_digest is not None and digest != expected_digest:
        raise PoolError(
            f"exclusion policy digest mismatch: file={digest} expected={expected_digest}"
        )
    return dict(policy), set(ids), digest


def _validate_canonical_value(value: Any, path: str = "$") -> None:
    """Refuse values whose JSON encoding is ambiguous or invalid for a digest."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise PoolError(
                    f"pool object key at {path} must be a string, "
                    f"got {type(key).__name__}"
                )
            _validate_canonical_value(child, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_canonical_value(child, f"{path}[{index}]")
        return
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        raise PoolError(
            f"pool value contains a float at {path}; floats are not permitted"
        )
    raise PoolError(
        f"pool value contains unsupported type {type(value).__name__} at {path}"
    )


def canonical_bytes(pool: Mapping[str, Any]) -> bytes:
    """Deterministic serialization. The digest is taken over exactly these bytes."""
    _validate_canonical_value(pool)
    return json.dumps(
        pool,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def pool_digest(pool: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(pool)).hexdigest()


def file_digest(path: pathlib.Path | str) -> str:
    """SHA-256 of the file's REAL bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_pool(
    *,
    task_ids: Sequence[str],
    registration_sha256: str,
    excluded: Sequence[Mapping[str, str]] = (),
    provenance: Mapping[str, Any] | None = None,
    forbidden_task_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a validated pool object. Refuses duplicates and malformed IDs."""
    if not isinstance(registration_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", registration_sha256
    ):
        raise PoolError("registration_sha256 must be a 64-hex digest")

    forbidden = {str(t) for t in forbidden_task_ids}
    seen: set[str] = set()
    for tid in task_ids:
        if not isinstance(tid, str) or not _ID_RE.match(tid):
            raise PoolError(f"malformed task id: {tid!r}")
        if tid in seen:
            raise PoolError(f"duplicate task id: {tid!r}")
        if tid in forbidden:
            raise PoolError(
                f"forbidden/excluded task {tid!r} cannot be included in task_ids"
            )
        seen.add(tid)

    exclusions = []
    excluded_seen: set[str] = set()
    for item in excluded:
        tid = str(item.get("task_id", ""))
        reason = str(item.get("reason", ""))
        if not reason:
            raise PoolError(f"exclusion for {tid!r} carries no reason")
        if tid in seen:
            raise PoolError(
                f"excluded task {tid!r} is also a member of task_ids; "
                "membership and exclusion are mutually exclusive"
            )
        if tid and tid not in seen and item.get("must_be_absent") is not True:
            # An exclusion of a task that is not in the pool is harmless only if
            # it is explicitly declared as absent; otherwise it signals a
            # generation defect and must be surfaced.
            raise PoolError(f"excluded task {tid!r} is not in the pool")
        excluded_seen.add(tid)
        exclusions.append({"task_id": tid, "reason": reason})

    missing_forbidden = sorted(forbidden - excluded_seen)
    if missing_forbidden:
        raise PoolError(
            f"{len(missing_forbidden)} required forbidden task(s) are missing from "
            f"pool.excluded: {missing_forbidden[:5]}"
        )

    return {
        "schema": POOL_SCHEMA,
        "registration_sha256": registration_sha256,
        "task_ids": sorted(seen),
        "excluded": sorted(exclusions, key=lambda e: e["task_id"]),
        "provenance": dict(provenance or {}),
    }


def validate_pool_object(pool: Any) -> dict[str, Any]:
    """Structural validation, independent of where the bytes came from."""
    if not isinstance(pool, Mapping):
        raise PoolError("pool is not a JSON object")
    if pool.get("schema") != POOL_SCHEMA:
        raise PoolError(f"unsupported pool schema: {pool.get('schema')!r}")
    ids = pool.get("task_ids")
    if not isinstance(ids, list) or not ids:
        raise PoolError("pool carries no task_ids list")
    if ids != sorted(ids):
        raise PoolError("pool task_ids are not sorted")
    if len(set(ids)) != len(ids):
        raise PoolError("pool task_ids contain duplicates")
    for tid in ids:
        if not isinstance(tid, str) or not _ID_RE.match(tid):
            raise PoolError(f"malformed task id in pool: {tid!r}")
    reg = pool.get("registration_sha256")
    if not isinstance(reg, str) or not re.fullmatch(r"[0-9a-f]{64}", reg):
        raise PoolError("pool registration_sha256 is missing or malformed")
    excluded = pool.get("excluded", [])
    if not isinstance(excluded, list):
        raise PoolError("pool excluded must be a list")
    excluded_ids: set[str] = set()
    for item in excluded:
        if not isinstance(item, Mapping):
            raise PoolError("pool exclusion entry is not an object")
        tid = item.get("task_id")
        reason = item.get("reason")
        if not isinstance(tid, str) or not tid:
            raise PoolError("pool exclusion entry carries no task_id")
        if tid in ids:
            raise PoolError(
                f"excluded task {tid!r} is also a member of task_ids; "
                "membership and exclusion are mutually exclusive"
            )
        if not isinstance(reason, str) or not reason:
            raise PoolError(f"exclusion for {tid!r} carries no reason")
        if tid in excluded_ids:
            raise PoolError(f"duplicate exclusion entry for {tid!r}")
        excluded_ids.add(tid)
    return dict(pool)


def load_pool(path: pathlib.Path | str) -> tuple[dict[str, Any], str]:
    """Load a pool file, returning ``(pool, digest_of_real_bytes)``.

    Rejects a file that is not byte-identical to its own canonical form, so a
    reordered or re-indented file cannot pass under an older digest.
    """
    p = pathlib.Path(path)
    raw = p.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        pool = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PoolError(f"pool is not valid UTF-8 JSON: {exc}") from exc
    pool = validate_pool_object(pool)
    if canonical_bytes(pool) != raw:
        raise PoolError("pool bytes are not in canonical form")
    return pool, digest


def verify_pool_file(
    path: pathlib.Path | str,
    *,
    expected_digest: str,
    expected_registration_sha256: str | None = None,
) -> dict[str, Any]:
    """Hash the REAL bytes, then compare against the signed expectation."""
    pool, digest = load_pool(path)
    if not isinstance(expected_digest, str) or digest != expected_digest:
        raise PoolError(
            f"pool digest mismatch: file is {digest}, authorization binds {expected_digest}"
        )
    if expected_registration_sha256 and pool["registration_sha256"] != expected_registration_sha256:
        raise PoolError(
            "pool is bound to a different registration digest: "
            f"{pool['registration_sha256']} != {expected_registration_sha256}"
        )
    return pool


def validate_manifest_membership(
    manifest_task_ids: Iterable[str], pool: Mapping[str, Any]
) -> list[str]:
    """Every manifest ID must be in the pool, and the manifest must be unique.

    Returns the sorted manifest IDs on success. Raises ``PoolError`` otherwise.
    This runs BEFORE any budget reservation, so an out-of-pool batch cannot
    consume capacity.
    """
    pool = validate_pool_object(pool)
    authorized = set(pool["task_ids"])
    ids = [str(t) for t in manifest_task_ids]
    if not ids:
        raise PoolError("manifest carries no task ids")
    if len(set(ids)) != len(ids):
        dupes = sorted({t for t in ids if ids.count(t) > 1})
        raise PoolError(f"manifest contains duplicate task ids: {dupes[:5]}")
    outside = sorted(set(ids) - authorized)
    if outside:
        raise PoolError(
            f"{len(outside)} task(s) are outside the authorized pool: {outside[:5]}"
        )
    return sorted(ids)
