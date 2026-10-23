"""Candidate-pool membership: explicit authorization, canonical bytes, real hashing.

The pool is the artifact an owner signs over. These tests pin that a registration
digest is not membership, that out-of-pool batches are refused, and that a
caller-supplied digest string can never substitute for hashing the real bytes.
"""

from __future__ import annotations

import json

import pytest

from qwen_train.f2_s8_candidate_pool import (
    POOL_SCHEMA,
    PoolError,
    build_pool,
    canonical_bytes,
    file_digest,
    load_pool,
    pool_digest,
    validate_manifest_membership,
    verify_pool_file,
)

REG = "a" * 64
IDS = ["acme__widget-1", "acme__gadget-2", "org__pkg-3"]


def _pool(**kw):
    return build_pool(task_ids=IDS, registration_sha256=REG, **kw)


def _write(tmp_path, pool):
    p = tmp_path / "pool.json"
    p.write_bytes(canonical_bytes(pool))
    return p


# ---------------------------------------------------------------------------
# Construction and canonicalisation
# ---------------------------------------------------------------------------


def test_ids_are_sorted_and_deduplicated_free(tmp_path):
    pool = build_pool(task_ids=list(reversed(IDS)), registration_sha256=REG)
    assert pool["task_ids"] == sorted(IDS)
    assert pool["schema"] == POOL_SCHEMA


def test_duplicate_ids_are_refused():
    with pytest.raises(PoolError, match="duplicate task id"):
        build_pool(task_ids=["a__b-1", "a__b-1"], registration_sha256=REG)


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a b", "", "a\\b"])
def test_malformed_ids_are_refused(bad):
    with pytest.raises(PoolError):
        build_pool(task_ids=[bad], registration_sha256=REG)


def test_registration_digest_must_be_hex64():
    with pytest.raises(PoolError, match="registration_sha256"):
        build_pool(task_ids=IDS, registration_sha256="not-a-digest")


def test_order_of_input_does_not_change_the_digest():
    a = build_pool(task_ids=IDS, registration_sha256=REG)
    b = build_pool(task_ids=list(reversed(IDS)), registration_sha256=REG)
    assert pool_digest(a) == pool_digest(b)


def test_exclusion_requires_a_reason():
    with pytest.raises(PoolError, match="no reason"):
        build_pool(task_ids=IDS, registration_sha256=REG,
                   excluded=[{"task_id": "acme__widget-1", "reason": ""}])


def test_excluding_a_non_member_is_surfaced():
    with pytest.raises(PoolError, match="not in the pool"):
        build_pool(task_ids=IDS, registration_sha256=REG,
                   excluded=[{"task_id": "other__x-9", "reason": "terminal"}])


# ---------------------------------------------------------------------------
# Real-bytes hashing, not a supplied string
# ---------------------------------------------------------------------------


def test_digest_is_over_the_written_bytes(tmp_path):
    pool = _pool()
    p = _write(tmp_path, pool)
    assert file_digest(p) == pool_digest(pool)


def test_reordered_or_reformatted_file_is_rejected(tmp_path):
    pool = _pool()
    p = tmp_path / "pool.json"
    p.write_text(json.dumps(pool, indent=2), encoding="utf-8")  # not canonical
    with pytest.raises(PoolError, match="not in canonical form"):
        load_pool(p)


def test_changed_bytes_fail_the_signed_digest(tmp_path):
    pool = _pool()
    p = _write(tmp_path, pool)
    signed = pool_digest(pool)
    tampered = dict(pool)
    tampered["task_ids"] = sorted(pool["task_ids"] + ["evil__x-1"])
    p.write_bytes(canonical_bytes(tampered))
    with pytest.raises(PoolError, match="digest mismatch"):
        verify_pool_file(p, expected_digest=signed)


def test_wrong_registration_binding_is_rejected(tmp_path):
    p = _write(tmp_path, _pool())
    with pytest.raises(PoolError, match="different registration digest"):
        verify_pool_file(p, expected_digest=pool_digest(_pool()),
                         expected_registration_sha256="b" * 64)


def test_valid_pool_verifies(tmp_path):
    pool = _pool()
    p = _write(tmp_path, pool)
    got = verify_pool_file(p, expected_digest=pool_digest(pool),
                           expected_registration_sha256=REG)
    assert got["task_ids"] == sorted(IDS)


def test_non_canonical_schema_is_rejected(tmp_path):
    p = tmp_path / "pool.json"
    p.write_text(json.dumps({"schema": "other/v9", "task_ids": ["a__b-1"]}),
                 encoding="utf-8")
    with pytest.raises(PoolError, match="unsupported pool schema"):
        load_pool(p)


def test_unsorted_pool_file_is_rejected(tmp_path):
    bad = {"schema": POOL_SCHEMA, "registration_sha256": REG,
           "task_ids": ["z__a-1", "a__b-2"], "excluded": [], "provenance": {}}
    p = tmp_path / "pool.json"
    p.write_bytes(canonical_bytes(bad))
    with pytest.raises(PoolError, match="not sorted"):
        load_pool(p)


# ---------------------------------------------------------------------------
# Manifest membership
# ---------------------------------------------------------------------------


def test_valid_subset_passes():
    pool = _pool()
    assert validate_manifest_membership(["org__pkg-3"], pool) == ["org__pkg-3"]


def test_out_of_pool_task_is_refused():
    pool = _pool()
    with pytest.raises(PoolError, match="outside the authorized pool"):
        validate_manifest_membership(["acme__widget-1", "evil__x-1"], pool)


def test_duplicate_manifest_ids_are_refused():
    pool = _pool()
    with pytest.raises(PoolError, match="duplicate task ids"):
        validate_manifest_membership(["acme__widget-1", "acme__widget-1"], pool)


def test_empty_manifest_is_refused():
    with pytest.raises(PoolError, match="no task ids"):
        validate_manifest_membership([], _pool())


def test_a_registration_digest_alone_is_not_membership():
    """The whole point: a task that is merely registered is not authorized."""
    pool = build_pool(task_ids=["acme__widget-1"], registration_sha256=REG)
    with pytest.raises(PoolError, match="outside the authorized pool"):
        validate_manifest_membership(["acme__gadget-2"], pool)
