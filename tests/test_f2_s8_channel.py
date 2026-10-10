"""Hostile-input tests for the disk-mediated channel reader (f2_s8_channel).

Evidence label: **Reader mechanics; hostile guest-written disk not tested live.**

Every byte string here is generated synthetically in a temp file.  No VM is
started, no disk is attached or mounted, no guest code runs, and no payload
value is ever executed.  What these tests establish is that the host reader
rejects malformed, oversized, mis-identified and hostile payloads **before** any
trusted state is touched.
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from qwen_train.f2_s8_channel import (
    CHANNEL_SCHEMA,
    MAX_FILE_BYTES,
    OPTIONAL_PAYLOAD_FIELDS,
    REQUIRED_PAYLOAD_FIELDS,
    decode_payload,
    read_channel_file,
)
from qwen_train.f2_s8_record import (
    HEADER_SIZE,
    MAGIC,
    MAX_PAYLOAD,
    encode_record,
)

JOB = "1" * 64


def _payload(**over):
    body = {
        "schema_version": CHANNEL_SCHEMA,
        "payload_kind": "plan",
        "task_id": "o__a-1",
        "job_id": JOB,
        "source": "swe-bench-live",
        "phase": "PLAN",
        "status": "planned",
        "created_at": "2026-10-10T00:00:00Z",
        "provenance": {"metadata": "pinned_population_artifact"},
    }
    body.update(over)
    return body


def _write(tmp_path: Path, data: bytes, name: str = "record.bin") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _header(
    data: bytes,
    *,
    magic: bytes | None = None,
    version: int | None = None,
    header_size: int | None = None,
    payload_len: int | None = None,
    digest: bytes | None = None,
) -> bytes:
    b = bytearray(data)
    if magic is not None:
        b[0:8] = magic
    if version is not None:
        b[8:10] = struct.pack("<H", version)
    if header_size is not None:
        b[10:12] = struct.pack("<H", header_size)
    if payload_len is not None:
        b[12:16] = struct.pack("<I", payload_len)
    if digest is not None:
        b[16:48] = digest
    return bytes(b)


def _raw_payload_bytes(obj) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _code(path: Path, **kw) -> str:
    v = read_channel_file(path, **kw)
    return v.reason_codes[0] if not v.ok else "OK"


class TestEnvelopeRejections:
    def test_valid_round_trip(self, tmp_path):
        p = _write(tmp_path, encode_record(_payload()))
        v = read_channel_file(p, expected_task_id="o__a-1", expected_job_id=JOB)
        assert v.ok is True
        assert v.stage == "validated"
        assert v.is_execution_evidence is False
        assert v.observed["total"] >= HEADER_SIZE

    def test_bad_magic(self, tmp_path):
        p = _write(tmp_path, _header(encode_record(_payload()), magic=b"NOTVHDX\x00"))
        assert _code(p) == "BAD_MAGIC"

    def test_unsupported_protocol_version(self, tmp_path):
        p = _write(tmp_path, _header(encode_record(_payload()), version=2))
        assert _code(p) == "UNSUPPORTED_VERSION"

    def test_truncated_header(self, tmp_path):
        p = _write(tmp_path, encode_record(_payload())[:20])
        assert _code(p) == "TRUNCATED_HEADER"

    def test_invalid_header_size_encoding(self, tmp_path):
        p = _write(tmp_path, _header(encode_record(_payload()), header_size=47))
        assert _code(p) == "INVALID_LENGTH"

    def test_oversized_declared_payload_length_is_refused_before_allocation(self, tmp_path):
        p = _write(tmp_path, _header(encode_record(_payload()), payload_len=0xFFFFFFFF))
        assert _code(p) == "OVERSIZED_PAYLOAD"

    def test_declared_length_above_the_cap_but_not_max_uint32(self, tmp_path):
        p = _write(
            tmp_path, _header(encode_record(_payload()), payload_len=MAX_PAYLOAD + 1)
        )
        assert _code(p) == "OVERSIZED_PAYLOAD"

    def test_truncated_payload(self, tmp_path):
        p = _write(tmp_path, encode_record(_payload())[:-6])
        assert _code(p) == "TRUNCATED_PAYLOAD"

    def test_digest_mismatch(self, tmp_path):
        p = _write(tmp_path, _header(encode_record(_payload()), digest=b"\x00" * 32))
        assert _code(p) == "DIGEST_MISMATCH"

    def test_trailing_garbage(self, tmp_path):
        p = _write(tmp_path, encode_record(_payload()) + b"trailing-garbage")
        assert _code(p) == "TRAILING_BYTES"

    def test_empty_payload(self, tmp_path):
        empty = b""
        rec = struct.pack("<8sHHI", MAGIC, 1, HEADER_SIZE, 0) + hashlib.sha256(empty).digest() + empty
        assert _code(_write(tmp_path, rec)) == "EMPTY_PAYLOAD"

    def test_invalid_utf8_payload(self, tmp_path):
        body = b"\xff\xfe\x00bad"
        rec = (
            struct.pack("<8sHHI", MAGIC, 1, HEADER_SIZE, len(body))
            + hashlib.sha256(body).digest()
            + body
        )
        assert _code(_write(tmp_path, rec)) == "INVALID_UTF8"

    def test_malformed_json_payload(self, tmp_path):
        body = b"{not json"
        rec = (
            struct.pack("<8sHHI", MAGIC, 1, HEADER_SIZE, len(body))
            + hashlib.sha256(body).digest()
            + body
        )
        assert _code(_write(tmp_path, rec)) == "MALFORMED_JSON"

    def test_non_object_json_payload(self, tmp_path):
        body = b"[1,2,3]"
        rec = (
            struct.pack("<8sHHI", MAGIC, 1, HEADER_SIZE, len(body))
            + hashlib.sha256(body).digest()
            + body
        )
        assert _code(_write(tmp_path, rec)) == "MALFORMED_JSON"

    def test_file_above_the_hard_cap_is_refused_without_reading_it(self, tmp_path):
        p = tmp_path / "huge.bin"
        p.write_bytes(b"\x00" * (MAX_FILE_BYTES + 1))
        assert _code(p) == "FILE_TOO_LARGE"

    def test_missing_file_is_an_io_error_not_a_crash(self, tmp_path):
        assert _code(tmp_path / "absent.bin") == "IO_ERROR"


class TestPayloadSchemaRejections:
    def _run(self, tmp_path, obj, **kw):
        return _code(_write(tmp_path, encode_record(obj)), **kw)

    @pytest.mark.parametrize("field", sorted(REQUIRED_PAYLOAD_FIELDS))
    def test_missing_required_field(self, tmp_path, field):
        body = _payload()
        del body[field]
        assert self._run(tmp_path, body) == "MISSING_FIELD"

    @pytest.mark.parametrize("field", ["command", "exec", "path", "module", "shell"])
    def test_forbidden_unknown_field_is_rejected(self, tmp_path, field):
        body = _payload(**{field: "rm -rf /"})
        assert self._run(tmp_path, body) == "UNEXPECTED_FIELD"

    def test_wrong_schema_version(self, tmp_path):
        assert self._run(tmp_path, _payload(schema_version="f2_s8_channel_v99")) == "WRONG_SCHEMA_VERSION"

    @pytest.mark.parametrize("status", ["exploded", "", "CLEAN"])
    def test_invalid_status(self, tmp_path, status):
        assert self._run(tmp_path, _payload(status=status)) == "INVALID_STATUS"

    @pytest.mark.parametrize("kind", ["exec", "", "result"])
    def test_invalid_payload_kind(self, tmp_path, kind):
        assert self._run(tmp_path, _payload(payload_kind=kind)) == "UNEXPECTED_FIELD"

    @pytest.mark.parametrize("phase", ["RUN", "", "EXECUTE"])
    def test_invalid_phase(self, tmp_path, phase):
        assert self._run(tmp_path, _payload(phase=phase)) == "UNEXPECTED_FIELD"

    def test_wrong_task_identity(self, tmp_path):
        assert self._run(tmp_path, _payload(), expected_task_id="other__task-9") == "WRONG_TASK_ID"

    def test_cross_task_artifact(self, tmp_path):
        """A record for task A delivered where task B is expected is refused."""
        assert self._run(tmp_path, _payload(task_id="taskA"), expected_task_id="taskB") == "WRONG_TASK_ID"

    def test_wrong_job_identity(self, tmp_path):
        assert self._run(tmp_path, _payload(), expected_job_id="2" * 64) == "WRONG_JOB_ID"

    def test_wrong_source(self, tmp_path):
        assert self._run(tmp_path, _payload(), expected_source="swe-rebench-v2") == "WRONG_SOURCE"

    def test_replayed_job_id(self, tmp_path):
        assert self._run(tmp_path, _payload(), seen_job_ids=[JOB]) == "REPLAYED_JOB_ID"

    def test_duplicate_re_delivery_of_the_same_bytes(self, tmp_path):
        assert self._run(tmp_path, _payload(), seen_job_ids={JOB}) == "REPLAYED_JOB_ID"

    def test_invalid_status_transition(self, tmp_path):
        assert (
            self._run(tmp_path, _payload(status="planned"), previous_status="rejected")
            == "INVALID_STATUS_TRANSITION"
        )

    def test_provenance_absent(self, tmp_path):
        assert self._run(tmp_path, _payload(provenance={})) == "PROVENANCE_ABSENT"

    def test_plan_presented_as_execution(self, tmp_path):
        assert (
            self._run(tmp_path, _payload(execution={"state": "base_fail_gold_pass"}))
            == "PLAN_PRESENTED_AS_EXECUTION"
        )


class TestGuestSuppliedValues:
    @pytest.mark.parametrize(
        "name",
        [
            "../escape.txt",
            "..\\escape.txt",
            "sub/dir/file.bin",
            "sub\\dir\\file.bin",
            "C:\\windows\\system32\\evil.dll",
            "file;rm -rf.bin",
            "file&calc.bin",
            "file|tee.bin",
            "file\nname.bin",
            "",
            "..",
            "a" * 300,
        ],
    )
    def test_guest_path_or_unsafe_name_is_refused(self, tmp_path, name):
        body = _payload(artifact={"name": name, "sha256": "a" * 64, "size_bytes": 1})
        assert self_run(tmp_path, body) == "UNTRUSTED_FIELD_VALUE"

    def test_artifact_digest_must_be_sha256(self, tmp_path):
        body = _payload(artifact={"name": "ok.bin", "sha256": "nope", "size_bytes": 1})
        assert self_run(tmp_path, body) == "MALFORMED_FIELD"

    def test_artifact_extra_keys_rejected(self, tmp_path):
        body = _payload(
            artifact={"name": "ok.bin", "sha256": "a" * 64, "size_bytes": 1, "command": "x"}
        )
        assert self_run(tmp_path, body) == "UNEXPECTED_FIELD"

    def test_reader_does_not_open_the_guest_named_artifact(self, tmp_path):
        """The reader returns without touching the named file — transport only."""
        body = _payload(artifact={"name": "guest-never-opened.bin", "sha256": "a" * 64, "size_bytes": 7})
        p = _write(tmp_path, encode_record(body))
        v = read_channel_file(p)
        assert v.ok is True
        assert not (tmp_path / "guest-never-opened.bin").exists()


def self_run(tmp_path, body, **kw):
    """Small helper so the parametrised cases above stay one line each."""
    return _code(_write(tmp_path, encode_record(body)), **kw)


class TestSizeBoundaries:
    def test_minimal_payload_is_accepted(self, tmp_path):
        assert read_channel_file(_write(tmp_path, encode_record(_payload()))).ok is True

    def test_payload_exactly_at_the_hard_cap_is_accepted(self, tmp_path):
        base = _payload()
        prefix = _raw_payload_bytes({**base, "note": ""})
        pad = MAX_PAYLOAD - len(prefix)
        assert pad > 0
        body = _payload(note="x" * pad)
        raw = _raw_payload_bytes(body)
        assert len(raw) == MAX_PAYLOAD
        rec = (
            struct.pack("<8sHHI", MAGIC, 1, HEADER_SIZE, len(raw))
            + hashlib.sha256(raw).digest()
            + raw
        )
        assert len(rec) == MAX_FILE_BYTES
        assert read_channel_file(_write(tmp_path, rec)).ok is True

    def test_payload_one_byte_over_the_cap_is_refused(self, tmp_path):
        body = _payload(note="y" * (MAX_PAYLOAD + 1))
        with pytest.raises(ValueError):
            encode_record(body)

    def test_large_but_under_cap_payload_is_accepted(self, tmp_path):
        body = _payload(note="z" * 200_000)
        v = read_channel_file(_write(tmp_path, encode_record(body)))
        assert v.ok is True
        assert len(v.payload["note"]) == 200_000


class TestRejectionLeavesStateUnchanged:
    def test_sink_is_only_called_on_full_validation(self, tmp_path):
        store: list[dict] = []
        good = read_channel_file(
            _write(tmp_path, encode_record(_payload()), "good.bin"),
            sink=store.append,
        )
        assert good.ok is True and len(store) == 1

        bad_path = _write(
            tmp_path, _header(encode_record(_payload()), digest=b"\x01" * 32), "bad.bin"
        )
        before = list(store)
        bad = read_channel_file(bad_path, sink=store.append)
        assert bad.ok is False
        assert store == before

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: _header(d, payload_len=0xFFFFFFFF),
            lambda d: _header(d, magic=b"XXXXXXXX"),
            lambda d: d + b"junk",
            lambda d: _header(d, digest=b"\x02" * 32),
        ],
    )
    def test_envelope_rejection_never_reaches_the_sink(self, tmp_path, mutate):
        store: list[dict] = []
        p = _write(tmp_path, mutate(encode_record(_payload())))
        assert read_channel_file(p, sink=store.append).ok is False
        assert store == []

    def test_schema_rejection_never_reaches_the_sink(self, tmp_path):
        store: list[dict] = []
        p = _write(tmp_path, encode_record(_payload(command="whoami")))
        v = read_channel_file(p, sink=store.append)
        assert v.reason_codes == ["UNEXPECTED_FIELD"]
        assert store == []


class TestPayloadHelpers:
    def test_decode_payload_rejects_empty_and_non_object(self):
        with pytest.raises(Exception, match="EMPTY_PAYLOAD"):
            decode_payload(b"")
        with pytest.raises(Exception, match="MALFORMED_JSON"):
            decode_payload(b"[]")

    def test_optional_fields_are_the_documented_set(self):
        assert set(OPTIONAL_PAYLOAD_FIELDS) == {"artifact", "execution", "note"}
