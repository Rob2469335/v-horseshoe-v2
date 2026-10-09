"""Stage 1 / Run 1 tests for the S8 disk-mediated channel components.

Scope: the bounded record format (:mod:`qwen_train.f2_s8_record`) and the
unattached VHDX reader (:mod:`qwen_train.f2_s8_vhdx`).  Synthetic only -- these
tests do NOT establish live isolation, S8 evidence, or F2 readiness.

Label: READER MECHANICS ONLY; A HOSTILE GUEST-WRITTEN DISK IS NOT TESTED.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from qwen_train.f2_s8_record import (
    HEADER_SIZE,
    MAGIC,
    MAX_PAYLOAD,
    RecordError,
    decode_record,
    encode_record,
)
from qwen_train.f2_s8_vhdx import VhdxError, inspect_vhdx, read_record_from_vhdx

SCRATCH_VHDX = Path(r"C:\Users\rober\F2_EVIDENCE\s8_stage1\scratch\f2s8-record.vhdx")


class TestRecordFormat:
    def test_round_trip(self):
        payload = {"schema": "f2-s8-synthetic/1", "ok": True, "n": 3}
        blob = encode_record(payload)
        assert blob[:8] == MAGIC
        assert blob[8:10] == struct.pack("<H", 1)
        got, observed = decode_record(blob)
        assert got == payload
        assert observed["total"] == len(blob)

    def test_encoding_is_deterministic(self):
        assert encode_record({"b": 1, "a": 2}) == encode_record({"a": 2, "b": 1})

    def test_header_size_is_48(self):
        assert HEADER_SIZE == 48
        blob = encode_record({"x": 1})
        assert struct.unpack_from("<H", blob, 10)[0] == 48

    @pytest.mark.parametrize("mutate", [
        "bad_magic", "bad_version", "bad_header_size", "oversized",
        "truncated_header", "truncated_payload", "digest", "trailing",
        "bad_utf8", "bad_json", "not_object", "empty",
    ])
    def test_rejections(self, mutate):
        good = bytearray(encode_record({"k": "v"}))
        if mutate == "bad_magic":
            good[0:8] = b"XXXXXXXX"
        elif mutate == "bad_version":
            good[8:10] = struct.pack("<H", 2)
        elif mutate == "bad_header_size":
            good[10:12] = struct.pack("<H", 40)
        elif mutate == "oversized":
            good[12:16] = struct.pack("<I", MAX_PAYLOAD + 1)
        elif mutate == "truncated_header":
            good = good[:20]
        elif mutate == "truncated_payload":
            good = good[:-1]
        elif mutate == "digest":
            good[16] ^= 0xFF
        elif mutate == "trailing":
            good = good + b"\x00"
        elif mutate == "bad_utf8":
            body = b"\xff\xfe not utf8"
            good = bytearray(encode_record({"k": "v"}))
            good[12:16] = struct.pack("<I", len(body))
            good[16:48] = __import__("hashlib").sha256(body).digest()
            good = good[:48] + bytearray(body)
        elif mutate == "bad_json":
            body = b"{not json"
            good = bytearray(encode_record({"k": "v"}))
            good[12:16] = struct.pack("<I", len(body))
            good[16:48] = __import__("hashlib").sha256(body).digest()
            good = good[:48] + bytearray(body)
        elif mutate == "not_object":
            body = b"[1,2,3]"
            good = bytearray(encode_record({"k": "v"}))
            good[12:16] = struct.pack("<I", len(body))
            good[16:48] = __import__("hashlib").sha256(body).digest()
            good = good[:48] + bytearray(body)
        elif mutate == "empty":
            good = b""
        with pytest.raises(RecordError):
            decode_record(bytes(good))

    def test_payload_larger_than_max_is_refused_at_encode(self):
        with pytest.raises(RecordError):
            encode_record({"blob": "x" * (MAX_PAYLOAD + 10)})


class TestVhdxReader:
    def test_non_vhdx_file_is_refused(self, tmp_path):
        p = tmp_path / "notavhdx.bin"
        p.write_bytes(b"\x00" * (256 * 1024))
        with pytest.raises(VhdxError):
            inspect_vhdx(p)

    def test_missing_file(self, tmp_path):
        with pytest.raises((VhdxError, FileNotFoundError, OSError)):
            inspect_vhdx(tmp_path / "absent.vhdx")

    @pytest.mark.skipif(not SCRATCH_VHDX.exists(), reason="scratch VHDX not present")
    def test_scratch_round_trip_and_metadata(self):
        info = inspect_vhdx(SCRATCH_VHDX)
        assert info.layout == "fixed"
        assert info.leave_blocks_allocated is True
        assert info.has_parent is False
        assert info.block_size & (info.block_size - 1) == 0
        assert info.payload_offset % (1024 * 1024) == 0
        assert info.payload_offset >= 192 * 1024
        payload, observed = read_record_from_vhdx(SCRATCH_VHDX)
        assert payload["payload"] == "hello-run1"
        assert observed["record_offset"] == info.payload_offset
        assert observed["bytes_read"] == observed["total"]

    @pytest.mark.skipif(not SCRATCH_VHDX.exists(), reason="scratch VHDX not present")
    def test_reader_does_not_attach(self):
        # The reader opens the file read-only; it must never call Mount-VHD.
        import qwen_train.f2_s8_vhdx as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        for banned in ("Mount-VHD", "New-VHD", "Dismount-VHD", "subprocess"):
            assert banned not in src, f"reader must not reference {banned}"
