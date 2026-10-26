"""Tests for the S8 disk-mediated host<->guest transport protocol.

These exercise the real envelope (:mod:`f2_s8_record`) and the real staged reader
(:mod:`f2_s8_channel`) against a local directory standing in for the guest
scratch disk.  No VM, disk image, network or guest process is touched: the
in-guest execution is the explicit ``execute_in_guest`` seam, simulated here by a
local writer that publishes a valid result.
"""
from __future__ import annotations

import hashlib

import pytest

from qwen_train.f2_s8_channel import CHANNEL_SCHEMA, read_bounded_bytes, read_channel_file
from qwen_train.f2_s8_record import encode_record
from qwen_train.f2_s8_runner import publish_atomic
from qwen_train.f2_s8_transport import (
    HostGuestChannel,
    TransportError,
    pending_requests,
    read_request,
    read_result,
    stage_request,
    write_result,
)

TASK = "o__a-1"
JOB = "job-abc"
SRC = "swe-bench-live"
PROV = {"producer": "s8-transport-test", "pinned": True}


def _artifact_name() -> str:
    return f"{TASK}.junit.xml"


class TestRequestRoundTrip:
    def test_staged_request_is_consumable_and_identity_checked(self, tmp_path):
        path = stage_request(
            channel_dir=tmp_path, task_id=TASK, job_id=JOB, source=SRC, provenance=PROV
        )
        verdict = read_request(
            path, expected_task_id=TASK, expected_job_id=JOB, expected_source=SRC
        )
        assert verdict.ok is True
        assert verdict.payload["payload_kind"] == "plan"
        assert verdict.payload["status"] == "planned"
        assert verdict.is_execution_evidence is False

    def test_wrong_identity_is_rejected(self, tmp_path):
        path = stage_request(
            channel_dir=tmp_path, task_id=TASK, job_id=JOB, source=SRC, provenance=PROV
        )
        verdict = read_request(path, expected_task_id="other")
        assert verdict.ok is False
        assert verdict.reason_codes == ["WRONG_TASK_ID"]


class TestResultRoundTrip:
    def test_published_result_is_consumable_and_carries_only_identity(self, tmp_path):
        data = b"<testsuites/>"
        path = write_result(
            channel_dir=tmp_path, task_id=TASK, job_id=JOB, source=SRC, provenance=PROV,
            artifact_name=_artifact_name(),
            artifact_sha256=hashlib.sha256(data).hexdigest(), artifact_size=len(data),
        )
        verdict = read_result(path, expected_task_id=TASK, expected_job_id=JOB)
        assert verdict.ok is True
        assert verdict.payload["payload_kind"] == "evidence"
        assert verdict.payload["artifact"]["sha256"] == hashlib.sha256(data).hexdigest()
        # transport validity is not scientific validity
        assert verdict.is_execution_evidence is False

    def test_replayed_result_is_rejected(self, tmp_path):
        path = write_result(
            channel_dir=tmp_path, task_id=TASK, job_id=JOB, source=SRC, provenance=PROV,
            artifact_name=_artifact_name(), artifact_sha256="a" * 64, artifact_size=1,
        )
        verdict = read_result(path, seen_job_ids={JOB})
        assert verdict.ok is False
        assert verdict.reason_codes == ["REPLAYED_JOB_ID"]

    def test_outcome_status_is_not_transportable(self, tmp_path):
        payload = {
            "schema_version": CHANNEL_SCHEMA, "payload_kind": "evidence",
            "task_id": TASK, "job_id": JOB, "source": SRC, "phase": "EXECUTE_S8",
            "status": "completed", "created_at": "2026-01-01T00:00:00Z",
            "provenance": PROV,
        }
        path = tmp_path / "bad.rec"
        publish_atomic(path, encode_record(payload))
        verdict = read_channel_file(path)
        assert verdict.ok is False
        assert verdict.reason_codes == ["OUTCOME_STATUS_NOT_TRANSPORTABLE"]

    def test_malformed_envelope_is_rejected(self, tmp_path):
        path = tmp_path / "junk.rec"
        path.write_bytes(b"not a record at all")
        verdict = read_channel_file(path)
        assert verdict.ok is False
        assert verdict.stage == "envelope"


class TestSafetyAndRecovery:
    def test_unsafe_path_component_is_refused(self, tmp_path):
        with pytest.raises(TransportError):
            stage_request(
                channel_dir=tmp_path, task_id="../escape", job_id=JOB, source=SRC,
                provenance=PROV,
            )

    def test_empty_provenance_is_refused(self, tmp_path):
        with pytest.raises(TransportError):
            stage_request(
                channel_dir=tmp_path, task_id=TASK, job_id=JOB, source=SRC, provenance={}
            )

    def test_bounded_read_refuses_oversized_file(self, tmp_path):
        path = tmp_path / "big.bin"
        path.write_bytes(b"x" * 10)
        with pytest.raises(Exception):
            read_bounded_bytes(path, max_total=4)

    def test_pending_requests_excludes_published_results(self, tmp_path):
        stage_request(channel_dir=tmp_path, task_id=TASK, job_id="job-1", source=SRC, provenance=PROV)
        stage_request(channel_dir=tmp_path, task_id=TASK, job_id="job-2", source=SRC, provenance=PROV)
        write_result(
            channel_dir=tmp_path, task_id=TASK, job_id="job-1", source=SRC, provenance=PROV,
            artifact_name=_artifact_name(), artifact_sha256="a" * 64, artifact_size=1,
        )
        pending = pending_requests(tmp_path)
        assert [p.name for p in pending] == ["job-2.rec"]


class TestHostGuestChannel:
    def test_dispatch_runs_the_guest_seam_and_validates_the_result(self, tmp_path):
        data = b"<testsuites/>"
        digest = hashlib.sha256(data).hexdigest()

        def guest(request_path, result_path):
            # The guest consumes the staged request, then publishes a result.
            verdict = read_request(request_path)
            assert verdict.ok
            write_result(
                channel_dir=tmp_path, task_id=TASK, job_id=JOB, source=SRC,
                provenance=PROV, artifact_name=_artifact_name(),
                artifact_sha256=digest, artifact_size=len(data),
            )

        from qwen_train.f2_s8_session import build_task_spec

        spec = build_task_spec(
            task_id=TASK, source=SRC, repo="o/a", base_commit="f" * 40,
            test_cmd="pytest -q", fail_to_pass=["t/t.py::test_a"],
            pass_to_pass=["t/t.py::test_b"], registration_id="reg",
            registration_sha256="c" * 64, evaluator={
                "evaluator_id": "e", "version": "1", "implementation_digest": "d" * 64,
                "procedure_id": "p", "protocol_version": "v",
            },
        )
        channel = HostGuestChannel(channel_dir=tmp_path, execute_in_guest=guest)
        verdict = channel.dispatch(spec=spec, job_id=JOB, provenance=PROV)
        assert verdict.ok is True
        assert verdict.payload["artifact"]["sha256"] == digest
