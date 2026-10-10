"""Fail-closed tests for the planned-task S8 job record (f2_s8_job_record_v1).

Synthetic records only: no real benchmark task, no task repository, no VM, no
model, no receipt key, no execution of any kind.  Every case below pins a
rejection the previous behaviour would have allowed or that the schema must
represent.
"""
from __future__ import annotations

import hashlib

import pytest

from qwen_train.f2_s8_job import (
    ALLOWED_TRANSITIONS,
    JOB_STATUSES,
    PHASES,
    SCHEMA_VERSION,
    JobRecordError,
    build_job_record,
    canonical_bytes,
    derive_job_id,
    validate_job_record,
)

_ART = {"name": "union_report.json", "sha256": "a" * 64}
_SRC = "b" * 64
_PROV = {"metadata": "pinned_population_artifact", "acquisition": "pinned_revision"}


def _rec(**over):
    kw = dict(
        task_id="o__a-1",
        source="swe-bench-live",
        source_record_digest=_SRC,
        population_artifact=dict(_ART),
        phase="PLAN",
        provenance=dict(_PROV),
    )
    kw.update(over)
    return build_job_record(**kw)


def _redigest(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "record_digest"}
    payload["record_digest"] = hashlib.sha256(canonical_bytes(body)).hexdigest()
    return payload


def _mutate(rec, **changes):
    payload = rec.to_dict() if hasattr(rec, "to_dict") else dict(rec)
    payload.update(changes)
    return _redigest(payload)


class TestWellFormedRecord:
    def test_planned_record_is_well_formed_but_never_execution_evidence(self):
        rec = _rec()
        v = validate_job_record(rec)
        assert v.ok is True
        assert rec.is_execution_evidence is False
        assert v.is_execution_evidence is False
        assert rec.s8_evidence_state == "not_established"

    def test_builder_sets_execution_state_to_not_established(self):
        rec = _rec()
        assert rec.to_dict()["execution_state"] == "not_established"
        assert rec.to_dict()["execution_facts"] == {}

    def test_job_id_is_deterministic_and_phase_bound(self):
        a = derive_job_id(
            task_id="t", source="s", population_artifact_sha256="a" * 64, phase="PLAN"
        )
        b = derive_job_id(
            task_id="t", source="s", population_artifact_sha256="a" * 64, phase="PLAN"
        )
        c = derive_job_id(
            task_id="t", source="s", population_artifact_sha256="a" * 64, phase="VALIDATE"
        )
        assert a == b
        assert a != c

    def test_digest_is_stable_and_covers_metadata(self):
        rec = _rec()
        again = build_job_record(
            task_id="o__a-1",
            source="swe-bench-live",
            source_record_digest=_SRC,
            population_artifact=dict(_ART),
            phase="PLAN",
            provenance=dict(_PROV),
            created_at=rec.to_dict()["created_at"],
        )
        assert again.record_digest == rec.record_digest
        other = _mutate(rec, metadata_facts={"language": "go"})
        assert other["record_digest"] != rec.record_digest

    @pytest.mark.parametrize("status", sorted(JOB_STATUSES))
    def test_every_status_is_representable_in_the_schema(self, status):
        assert status in ALLOWED_TRANSITIONS
        assert status in JOB_STATUSES

    @pytest.mark.parametrize("phase", sorted(PHASES))
    def test_every_phase_is_representable(self, phase):
        assert _rec(phase=phase).phase == phase


class TestBuilderRefusesFabrication:
    def test_completed_status_is_refused(self):
        with pytest.raises(JobRecordError, match="execution outcome"):
            _rec(status="completed")

    def test_failed_status_is_refused(self):
        with pytest.raises(JobRecordError, match="execution outcome"):
            _rec(status="failed")

    def test_contamination_claim_is_refused(self):
        with pytest.raises(JobRecordError, match="contamination class"):
            _rec(metadata_facts={"contamination_class": "CLEAN"})

    def test_raw_test_cmd_is_refused(self):
        with pytest.raises(JobRecordError, match="test_cmd"):
            _rec(metadata_facts={"test_cmd": "pytest -q"})

    @pytest.mark.parametrize("phase", ["nope", "", "PLAN;rm"])
    def test_unknown_phase_is_refused(self, phase):
        with pytest.raises(JobRecordError, match="phase"):
            _rec(phase=phase)

    @pytest.mark.parametrize("status", ["exploded", "", "clean"])
    def test_unknown_status_is_refused(self, status):
        with pytest.raises(JobRecordError, match="status"):
            _rec(status=status)

    def test_empty_provenance_is_refused(self):
        with pytest.raises(JobRecordError, match="provenance"):
            _rec(provenance={})

    def test_population_artifact_requires_name_and_digest(self):
        with pytest.raises(JobRecordError, match="population_artifact"):
            _rec(population_artifact={"name": "x"})


class TestFailClosedValidation:
    def test_missing_required_field(self):
        payload = _rec().to_dict()
        del payload["source_record_digest"]
        payload = _redigest(payload)
        assert validate_job_record(payload).reason_codes == ["MISSING_FIELD"]

    def test_empty_record(self):
        assert validate_job_record({}).reason_codes == ["MISSING_FIELD"]

    def test_wrong_schema_version(self):
        payload = _mutate(_rec().to_dict(), schema_version="f2_s8_job_record_v999")
        assert "WRONG_SCHEMA_VERSION" in validate_job_record(payload).reason_codes

    def test_wrong_task_identity(self):
        rec = _rec()
        assert (
            "WRONG_TASK_ID"
            in validate_job_record(rec, expected_task_id="other__task-9").reason_codes
        )

    def test_wrong_job_identity(self):
        payload = _mutate(_rec().to_dict(), job_id="f" * 64)
        assert "WRONG_JOB_ID" in validate_job_record(payload).reason_codes

    def test_mismatched_source_identity(self):
        rec = _rec()
        assert (
            "SOURCE_MISMATCH"
            in validate_job_record(rec, expected_source="swe-rebench-v2").reason_codes
        )
        assert (
            "SOURCE_MISMATCH"
            in validate_job_record(
                rec, expected_population_sha256="c" * 64
            ).reason_codes
        )

    def test_hash_mismatch(self):
        payload = _rec().to_dict()
        # A field that identity derivation does not cover, digest left stale.
        payload["created_at"] = "2020-01-01T00:00:00"
        assert "DIGEST_MISMATCH" in validate_job_record(payload).reason_codes

    def test_non_hex_digest_field_is_malformed(self):
        payload = _mutate(_rec().to_dict(), source_record_digest="not-a-digest")
        assert "MALFORMED_FIELD" in validate_job_record(payload).reason_codes

    def test_replayed_job_id(self):
        rec = _rec()
        v = validate_job_record(rec, seen_job_ids=[rec.job_id])
        assert v.reason_codes == ["REPLAYED_JOB_ID"]

    def test_duplicate_record(self):
        rec = _rec()
        v = validate_job_record(rec, seen_records={rec.job_id: rec.record_digest})
        assert v.reason_codes == ["DUPLICATE_RECORD"]

    def test_replay_with_a_different_payload_under_the_same_job_id(self):
        rec = _rec()
        v = validate_job_record(rec, seen_records={rec.job_id: "d" * 64})
        assert v.reason_codes == ["REPLAYED_JOB_ID"]

    @pytest.mark.parametrize(
        "previous,current",
        [
            ("completed", "planned"),
            ("rejected", "attempted"),
            ("not_established", "attempted"),
            ("completed", "attempted"),
            ("planned", "completed"),
        ],
    )
    def test_invalid_status_transition(self, previous, current):
        payload = _mutate(_rec().to_dict(), status=current)
        v = validate_job_record(payload, previous_status=previous)
        assert v.reason_codes == ["INVALID_STATUS_TRANSITION"]

    @pytest.mark.parametrize("previous", sorted(JOB_STATUSES))
    def test_attempted_is_only_reachable_from_planned_or_a_failed_retry(self, previous):
        payload = _mutate(_rec().to_dict(), status="attempted")
        v = validate_job_record(payload, previous_status=previous)
        assert v.ok is (previous in ("planned", "failed"))

    def test_invalid_status_value(self):
        payload = _mutate(_rec().to_dict(), status="exploded")
        assert "INVALID_STATUS" in validate_job_record(payload).reason_codes

    def test_inconsistent_evidence_from_different_sources(self):
        rec = _rec()
        v = validate_job_record(
            rec, known_source_digests={"o__a-1": "e" * 64}
        )
        assert v.reason_codes == ["CROSS_SOURCE_INCONSISTENT"]

    @pytest.mark.parametrize(
        "changes",
        [
            {"execution_state": "base_failed_gold_passed"},
            {"execution_facts": {"base": "fail"}},
            {"executed": True},
        ],
    )
    def test_plan_presented_as_execution_is_refused(self, changes):
        payload = _mutate(_rec().to_dict(), **changes)
        v = validate_job_record(payload)
        assert v.reason_codes == ["PLAN_PRESENTED_AS_EXECUTION"]

    def test_provenance_absent(self):
        payload = _mutate(_rec().to_dict(), provenance={})
        assert "PROVENANCE_ABSENT" in validate_job_record(payload).reason_codes

    @pytest.mark.parametrize("klass", ["CLEAN", "POTENTIALLY CONTAMINATED", "UNKNOWN"])
    def test_cleanliness_cannot_be_asserted_by_a_job_record(self, klass):
        payload = _mutate(
            _rec().to_dict(), metadata_facts={"contamination_class": klass}
        )
        v = validate_job_record(payload)
        assert v.reason_codes == ["CLEAN_NOT_ASSERTABLE_BY_JOB_RECORD"]
        assert v.ok is False

    def test_crafting_clean_with_authority_and_evidence_still_refused_here(self):
        """Authority + evidence do not route through a job record: admission and
        contamination stay with f2_population/f2_evidence, so this record type
        refuses the claim outright rather than half-trusting it."""
        payload = _mutate(
            _rec().to_dict(),
            metadata_facts={"contamination_class": "CLEAN"},
            authority_reference="F2-IMPL-AUTH-028",
            execution_evidence_digest="f" * 64,
        )
        assert "CLEAN_NOT_ASSERTABLE_BY_JOB_RECORD" in validate_job_record(
            payload
        ).reason_codes

    def test_raw_test_cmd_in_metadata_is_refused(self):
        payload = _mutate(
            _rec().to_dict(), metadata_facts={"test_cmd": "cmd /c del /q *"}
        )
        assert "UNTRUSTED_FIELD_VALUE" in validate_job_record(payload).reason_codes


class TestNeverReadiness:
    @pytest.mark.parametrize(
        "status", ["planned", "attempted", "rejected", "not_established"]
    )
    def test_ok_record_still_reports_no_evidence(self, status):
        rec = _rec(status=status)
        v = validate_job_record(rec)
        assert v.ok is True
        assert rec.s8_evidence_state == "not_established"
        assert bool(v) is True  # record well-formedness only
        assert v.is_execution_evidence is False

    def test_schema_version_is_the_documented_one(self):
        assert SCHEMA_VERSION == "f2_s8_job_record_v1"
