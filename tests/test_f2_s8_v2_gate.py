"""v2_preflight gate: stable refusal codes, read-once proof, no side effects.

Every fixture is synthetic and temporary: no production grant, key, ledger,
budget root, registration or pool is read or written.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from qwen_train.f2_s8_candidate_pool import build_pool, canonical_bytes, pool_digest
from qwen_train.f2_s8_factory import ADMITTED_OUTCOME, AppendOnlyLedger
from qwen_train.f2_s8_v2_gate import (
    CODES,
    PRECEDENCE,
    V2GateError,
    v2_preflight,
)

CONTENT = "c" * 64
IDS = ["acme__widget-1", "acme__gadget-2"]
REG_BYTES = b'["acme__widget-1","acme__gadget-2"]'


def _write_registration(tmp_path, data=REG_BYTES, *, content=CONTENT, prov=None):
    reg = tmp_path / "registered_tasks.json"
    reg.write_bytes(data)
    raw = hashlib.sha256(data).hexdigest()
    payload = {"tasks_sha256": raw, "registration_content_sha256": content}
    payload.update(prov or {})
    (tmp_path / "registered_tasks.provenance.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return reg, raw


def _write_pool(tmp_path, *, ids=IDS, excluded=(), content=CONTENT,
                registration_raw=None, anchor=True):
    provenance = {}
    if anchor:
        provenance["registration_file_sha256"] = registration_raw or hashlib.sha256(
            REG_BYTES
        ).hexdigest()
    pool = build_pool(
        task_ids=ids,
        registration_sha256=content,
        excluded=excluded,
        provenance=provenance,
    )
    pool_file = tmp_path / "pool.json"
    pool_file.write_bytes(canonical_bytes(pool))
    return pool, pool_file


def _fixture(tmp_path, *, ids=IDS, excluded=(), not_before="2020-01-01T00:00:00+00:00",
             not_after="2099-01-01T00:00:00+00:00", schema="f2-authorization/v2",
             reg_bytes=REG_BYTES, content=CONTENT, anchor=True):
    reg, raw = _write_registration(tmp_path, reg_bytes, content=content)
    pool, pool_file = _write_pool(
        tmp_path, ids=ids, excluded=excluded, content=content,
        registration_raw=raw, anchor=anchor,
    )
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    record = {
        "id": "v2-test",
        "schema": schema,
        "stage": "S8-001",
        "operations": ["base_gold_production"],
        "not_before": not_before,
        "not_after": not_after,
        "registration_sha256": content,
        "candidate_pool_digest": pool_digest(pool),
    }
    return record, reg, pool_file, ledger, pool


def _call(record, reg, pool_file, ledger, manifest=None):
    return v2_preflight(
        record=record,
        registration_file=reg,
        candidate_pool_file=pool_file,
        manifest_task_ids=manifest if manifest is not None else ["acme__widget-1"],
        ledger_paths=[ledger],
        manifest_digest="m" * 64,
    )


def _code(record, reg, pool_file, ledger, manifest=None) -> str:
    with pytest.raises(V2GateError) as caught:
        _call(record, reg, pool_file, ledger, manifest)
    return caught.value.code


# ---------------------------------------------------------------------------
# happy path + evidence object
# ---------------------------------------------------------------------------


def test_valid_subset_passes_and_returns_frozen_evidence(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    ev = _call(record, reg, pool_file, ledger)

    assert ev.task_ids == ("acme__widget-1",)
    assert ev.registration_sha256 == CONTENT
    assert ev.registration_file_sha256 == hashlib.sha256(REG_BYTES).hexdigest()
    assert ev.candidate_pool_digest == record["candidate_pool_digest"]
    assert ev.manifest_digest == "m" * 64
    assert ev.eligibility == {"acme__widget-1": "clear"}

    # Nested data is really frozen: a tuple cannot be appended to, the mapping
    # cannot be mutated, and as_evidence() hands back copies.
    with pytest.raises(AttributeError):
        ev.task_ids.append("evil__x-1")  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        ev.eligibility["evil__x-1"] = "clear"  # type: ignore[index]
    copied = ev.as_evidence()
    copied["task_ids"].append("evil__x-1")
    assert ev.task_ids == ("acme__widget-1",)


def test_gate_has_no_side_effects(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in tmp_path.iterdir() if p.is_file()}
    _call(record, reg, pool_file, ledger)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in tmp_path.iterdir() if p.is_file()}
    assert after == before


def test_precedence_table_matches_the_code_set():
    assert set(PRECEDENCE) == set(CODES)
    assert len(PRECEDENCE) == len(set(PRECEDENCE))


# ---------------------------------------------------------------------------
# grant refusals
# ---------------------------------------------------------------------------


def test_wrong_schema_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path, schema="f2-authorization/v1")
    assert _code(record, reg, pool_file, ledger) == "WRONG_SCHEMA"


def test_expired_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(
        tmp_path, not_after="2020-01-02T00:00:00+00:00"
    )
    assert _code(record, reg, pool_file, ledger) == "EXPIRED"


def test_not_yet_valid_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(
        tmp_path, not_before="2098-01-01T00:00:00+00:00", not_after="2099-01-01T00:00:00+00:00"
    )
    assert _code(record, reg, pool_file, ledger) == "NOT_YET_VALID"


def test_malformed_timestamp_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path, not_before="not-a-date")
    assert _code(record, reg, pool_file, ledger) == "MALFORMED_TIMESTAMP"


def test_non_mapping_grant_refused(tmp_path):
    _, reg, pool_file, ledger, _ = _fixture(tmp_path)
    with pytest.raises(V2GateError) as caught:
        v2_preflight(
            record="not-a-record",  # type: ignore[arg-type]
            registration_file=reg,
            candidate_pool_file=pool_file,
            manifest_task_ids=["acme__widget-1"],
            ledger_paths=[ledger],
        )
    assert caught.value.code == "INVALID_GRANT"


# ---------------------------------------------------------------------------
# registration refusals
# ---------------------------------------------------------------------------


def test_missing_registration_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    reg.unlink()
    assert _code(record, reg, pool_file, ledger) == "MISSING_ARTIFACT"


def test_missing_provenance_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    (tmp_path / "registered_tasks.provenance.json").unlink()
    assert _code(record, reg, pool_file, ledger) == "MISSING_ARTIFACT"


def test_unreadable_provenance_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    (tmp_path / "registered_tasks.provenance.json").write_bytes(b"{not json")
    assert _code(record, reg, pool_file, ledger) == "PROVENANCE_INVALID"


def test_provenance_without_content_digest_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    (tmp_path / "registered_tasks.provenance.json").write_text(
        json.dumps({"tasks_sha256": hashlib.sha256(REG_BYTES).hexdigest()}),
        encoding="utf-8",
    )
    assert _code(record, reg, pool_file, ledger) == "PROVENANCE_INVALID"


def test_modified_registration_bytes_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    reg.write_bytes(b'["different"]')
    assert _code(record, reg, pool_file, ledger) == "REGISTRATION_BYTES_CHANGED"


def test_unparseable_registration_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(
        tmp_path, reg_bytes=b'["acme__widget-1"'
    )
    # The provenance and the signed pool both declare these exact bytes, so the
    # bytes are authentic; what fails is parsing them as the ID list.
    assert _code(record, reg, pool_file, ledger) == "REGISTRATION_UNREADABLE"


def test_grant_registration_digest_mismatch_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    record = dict(record, registration_sha256="d" * 64)
    assert _code(record, reg, pool_file, ledger) == "PROVENANCE_DIGEST_MISMATCH"
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    record = dict(record, registration_sha256="d" * 64)
    assert _code(record, reg, pool_file, ledger) == "PROVENANCE_DIGEST_MISMATCH"


def test_registration_with_duplicate_ids_refused(tmp_path):
    payload = b'["acme__widget-1","acme__widget-1"]'
    record, reg, pool_file, ledger, _ = _fixture(tmp_path, reg_bytes=payload)
    # The provenance declares the real bytes, so the failure is the ID list.
    assert _code(record, reg, pool_file, ledger) == "REGISTRATION_UNREADABLE"


# ---------------------------------------------------------------------------
# pool refusals
# ---------------------------------------------------------------------------


def test_pool_with_an_unsupported_schema_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    pool_file.write_bytes(b'{"schema":"f2-s8-candidate-pool/v9","task_ids":["acme__widget-1"]}')
    assert _code(record, reg, pool_file, ledger) == "POOL_INVALID"


def test_modified_pool_bytes_refused(tmp_path):
    record, reg, pool_file, ledger, pool = _fixture(tmp_path)
    tampered = dict(pool)
    tampered["task_ids"] = sorted(pool["task_ids"] + ["evil__x-1"])
    pool_file.write_bytes(canonical_bytes(tampered))
    assert _code(record, reg, pool_file, ledger) == "POOL_BYTES_CHANGED"


def test_pool_bound_to_a_different_registration_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    _write_pool(other_dir, ids=IDS, content="e" * 64)
    other = other_dir / "pool.json"
    pool_file.write_bytes(other.read_bytes())
    record = dict(
        record, candidate_pool_digest=hashlib.sha256(other.read_bytes()).hexdigest()
    )
    # A different registration content digest is refused by the pool itself.
    from qwen_train.f2_s8_candidate_pool import PoolError, verify_pool_file

    with pytest.raises(PoolError, match="different registration digest"):
        verify_pool_file(
            pool_file,
            expected_digest=hashlib.sha256(other.read_bytes()).hexdigest(),
            expected_registration_sha256=CONTENT,
        )
    assert _code(record, reg, pool_file, ledger) == "POOL_BINDING_MISMATCH"


def test_pool_without_a_registration_anchor_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path, anchor=False)
    assert _code(record, reg, pool_file, ledger) == "REGISTRATION_BINDING_MISSING"


def test_pool_ids_absent_from_the_registration_refused(tmp_path):
    payload = b'["acme__widget-1"]'
    record, reg, pool_file, ledger, _ = _fixture(tmp_path, reg_bytes=payload)
    assert _code(record, reg, pool_file, ledger) == "POOL_NOT_IN_REGISTRATION"


# ---------------------------------------------------------------------------
# manifest refusals + precedence
# ---------------------------------------------------------------------------


def test_out_of_pool_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    assert _code(record, reg, pool_file, ledger, ["evil__x-1"]) == "OUT_OF_POOL"


def test_excluded_task_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(
        tmp_path,
        ids=["acme__widget-1"],
        excluded=[{"task_id": "acme__gadget-2", "reason": "terminal",
                   "must_be_absent": True}],
    )
    assert _code(record, reg, pool_file, ledger, ["acme__gadget-2"]) == "EXCLUDED_TASK"


def test_duplicate_manifest_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    assert _code(
        record, reg, pool_file, ledger,
        ["acme__widget-1", "acme__widget-1"],
    ) == "DUPLICATE_TASK"


def test_empty_manifest_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    assert _code(record, reg, pool_file, ledger, []) == "EMPTY_MANIFEST"


def test_excluded_outranks_out_of_pool_and_revert_then_pass(tmp_path):
    """Documented precedence, proven by changing ONE condition at a time.

    ``acme__gadget-2`` is excluded AND outside ``task_ids``. Step 1 proves the
    exclusion wins. Step 2 removes only the exclusion, so the same manifest is
    now merely out of pool. Step 3 restores the valid fixture.
    """
    record, reg, pool_file, ledger, _ = _fixture(
        tmp_path,
        ids=["acme__widget-1"],
        excluded=[{"task_id": "acme__gadget-2", "reason": "terminal",
                   "must_be_absent": True}],
    )
    # 1. excluded + out of pool -> the owner's exclusion decides.
    assert _code(record, reg, pool_file, ledger, ["acme__gadget-2"]) == "EXCLUDED_TASK"

    # 2. change ONLY the exclusion.
    pool, pool_file = _write_pool(tmp_path, ids=["acme__widget-1"])
    record = dict(record, candidate_pool_digest=pool_digest(pool))
    assert _code(record, reg, pool_file, ledger, ["acme__gadget-2"]) == "OUT_OF_POOL"

    # 3. restore the valid state -> the gate passes.
    ev = _call(record, reg, pool_file, ledger, ["acme__widget-1"])
    assert ev.task_ids == ("acme__widget-1",)


# ---------------------------------------------------------------------------
# ledger refusals
# ---------------------------------------------------------------------------


def _ledger_row(path, task_id, *, outcome, outcome_class="scientific"):
    AppendOnlyLedger(path).append(
        {"task_id": task_id, "outcome": outcome, "outcome_class": outcome_class}
    )


def test_credited_task_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    _ledger_row(ledger, "acme__widget-1", outcome=ADMITTED_OUTCOME)
    assert _code(record, reg, pool_file, ledger) == "CREDITED_TASK"


def test_credited_outranks_terminal(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    _ledger_row(ledger, "acme__widget-1", outcome=ADMITTED_OUTCOME)
    assert _code(record, reg, pool_file, ledger) == "CREDITED_TASK"


def test_terminal_task_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    _ledger_row(ledger, "acme__widget-1", outcome="rejected")
    assert _code(record, reg, pool_file, ledger) == "TERMINAL_TASK"


def test_invalid_ledger_evidence_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    ledger.write_text('{"seq": 4}\n', encoding="utf-8")
    assert _code(record, reg, pool_file, ledger) == "LEDGER_INVALID"


def test_no_ledger_refused(tmp_path):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    with pytest.raises(V2GateError) as caught:
        v2_preflight(
            record=record,
            registration_file=reg,
            candidate_pool_file=pool_file,
            manifest_task_ids=["acme__widget-1"],
            ledger_paths=[],
        )
    assert caught.value.code == "LEDGER_INVALID"


# ---------------------------------------------------------------------------
# registration/provenance coordinated tampering (the old residual gap)
# ---------------------------------------------------------------------------


def test_coordinated_registration_and_provenance_tamper_refused(tmp_path):
    """Rewrite the registration AND its tasks_sha256, leaving the content digest.

    This is the exact attack the provenance chain alone could not see: the grant
    binds ``registration_content_sha256``, which the attacker leaves untouched,
    and ``tasks_sha256`` is updated to the new bytes. It is now refused because
    the SIGNED candidate pool also binds the registration's raw-byte digest --
    the attacker cannot change that without changing the pool, and the pool's
    digest is fixed inside the signed grant.
    """
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)

    tampered = b'["acme__widget-1","acme__gadget-2","evil__x-9"]'
    reg.write_bytes(tampered)
    (tmp_path / "registered_tasks.provenance.json").write_text(
        json.dumps({
            "tasks_sha256": hashlib.sha256(tampered).hexdigest(),
            "registration_content_sha256": CONTENT,  # unchanged on purpose
        }),
        encoding="utf-8",
    )
    # The grant is unchanged, and the pool still lists only ratified tasks.
    assert _code(record, reg, pool_file, ledger) == "REGISTRATION_BYTES_CHANGED"


def test_coordinated_tamper_that_also_rewrites_the_pool_is_still_refused(tmp_path):
    """Even a matching pool rewrite must change the SIGNED pool digest."""
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)

    tampered = b'["acme__widget-1","acme__gadget-2","evil__x-9"]'
    reg.write_bytes(tampered)
    raw = hashlib.sha256(tampered).hexdigest()
    (tmp_path / "registered_tasks.provenance.json").write_text(
        json.dumps({"tasks_sha256": raw, "registration_content_sha256": CONTENT}),
        encoding="utf-8",
    )
    pool, pool_file = _write_pool(
        tmp_path, ids=IDS, content=CONTENT, registration_raw=raw
    )
    # The pool file is self-consistent with the new registration, but its bytes
    # no longer hash to the digest in the (unchanged) signed grant.
    assert _code(record, reg, pool_file, ledger) == "POOL_BYTES_CHANGED"
    assert pool_digest(pool) != record["candidate_pool_digest"]


# ---------------------------------------------------------------------------
# READ-ONCE: a second read would see different bytes
# ---------------------------------------------------------------------------


def _count_reads_of(monkeypatch, target: pathlib.Path, replacement: bytes) -> dict:
    """Count reads of ``target`` and swap its bytes before any SECOND read.

    Deterministic: no sleeps, no scheduler dependence. If the implementation
    hashed one read and parsed a second, the second read would return
    ``replacement`` and the gate could not return the original digest.
    """
    real = pathlib.Path.read_bytes
    state = {"reads": 0}

    def wrapper(self):
        if self == target:
            state["reads"] += 1
            if state["reads"] == 2:
                target.write_bytes(replacement)
        return real(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", wrapper)
    return state


def test_registration_is_read_exactly_once(tmp_path, monkeypatch):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    original = hashlib.sha256(REG_BYTES).hexdigest()
    state = _count_reads_of(monkeypatch, reg, b'["evil__x-9"]')

    ev = _call(record, reg, pool_file, ledger)

    assert state["reads"] == 1, "the registration was reopened"
    assert ev.registration_file_sha256 == original
    assert reg.read_bytes() == b'["evil__x-9"]'  # the swap really happened


def test_registration_provenance_is_read_exactly_once(tmp_path, monkeypatch):
    record, reg, pool_file, ledger, _ = _fixture(tmp_path)
    prov = tmp_path / "registered_tasks.provenance.json"
    state = _count_reads_of(monkeypatch, prov, b'{"tasks_sha256": "f' + b'0' * 63 + b'"}')

    ev = _call(record, reg, pool_file, ledger)

    assert state["reads"] == 1, "the provenance record was reopened"
    assert ev.registration_sha256 == CONTENT


def test_candidate_pool_is_read_exactly_once(tmp_path, monkeypatch):
    record, reg, pool_file, ledger, pool = _fixture(tmp_path)
    replacement = dict(pool)
    replacement["task_ids"] = sorted(pool["task_ids"] + ["evil__x-1"])
    state = _count_reads_of(monkeypatch, pool_file, canonical_bytes(replacement))

    ev = _call(record, reg, pool_file, ledger)

    assert state["reads"] == 1, "the candidate pool was reopened"
    assert ev.candidate_pool_digest == record["candidate_pool_digest"]
    assert ev.pool_task_ids == tuple(pool["task_ids"])
