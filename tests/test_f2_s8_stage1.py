"""Stage 1 / Run 1 tests for the S8 disk-mediated channel components.

Scope: the bounded record format (:mod:`qwen_train.f2_s8_record`) and the
hardened, unattached VHDX reader (:mod:`qwen_train.f2_s8_vhdx`).

All VHDX tests use a **self-contained synthetic fixture** built in a temp file;
no disk is ever attached or mounted.  Synthetic only -- these tests do NOT
establish live isolation, S8 evidence, or F2 readiness.

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
from qwen_train.f2_s8_vhdx import (
    GUID_BAT,
    GUID_FILE_PARAMETERS,
    GUID_LOGICAL_SECTOR,
    GUID_METADATA,
    GUID_PHYSICAL_SECTOR,
    GUID_VIRTUAL_DISK_ID,
    GUID_VIRTUAL_DISK_SIZE,
    VhdxError,
    bat_payload_index,
    bat_sb_index,
    chunk_ratio_for,
    crc32c,
    inspect_vhdx,
    read_record_from_vhdx,
)

MB = 1024 * 1024
HEADER_OFFS = (64 * 1024, 128 * 1024)
REGION_OFFS = (192 * 1024, 256 * 1024)
META_OFF, META_LEN = 2 * MB, 1 * MB
BAT_OFF, BAT_LEN = 3 * MB, 1 * MB
PAYLOAD_BASE = 4 * MB
BLOCK = 1 * MB
VDS = 8 * MB
BLOCK_COUNT = VDS // BLOCK
FILE_SIZE = 12 * MB
SCRATCH_VHDX = Path(r"C:\Users\rober\F2_EVIDENCE\s8_stage1\scratch\f2s8-record.vhdx")


# --------------------------------------------------------------------------- #
# Synthetic VHDX fixture builder
# --------------------------------------------------------------------------- #
def _seal_header(buf: bytearray, off: int) -> None:
    buf[off + 4:off + 8] = b"\x00\x00\x00\x00"
    struct.pack_into("<I", buf, off + 4, crc32c(bytes(buf[off:off + 4096])))


def _seal_region(buf: bytearray, off: int) -> None:
    buf[off + 4:off + 8] = b"\x00\x00\x00\x00"
    struct.pack_into("<I", buf, off + 4, crc32c(bytes(buf[off:off + 64 * 1024])))


def _put_header(buf: bytearray, off: int, seq: int) -> None:
    buf[off:off + 4096] = b"\x00" * 4096
    buf[off:off + 4] = b"head"
    struct.pack_into("<Q", buf, off + 8, seq)
    struct.pack_into("<H", buf, off + 66, 1)  # Version MUST be 1
    _seal_header(buf, off)


def _put_region_table(buf: bytearray, off: int, entries) -> None:
    buf[off:off + 64 * 1024] = b"\x00" * (64 * 1024)
    buf[off:off + 4] = b"regi"
    struct.pack_into("<I", buf, off + 8, len(entries))
    for i, (guid, file_off, length, required) in enumerate(entries):
        base = off + 16 + i * 32
        buf[base:base + 16] = guid
        struct.pack_into("<QII", buf, base + 16, file_off, length, required)
    _seal_region(buf, off)


def _put_metadata(buf: bytearray, items) -> None:
    buf[META_OFF:META_OFF + META_LEN] = b"\x00" * META_LEN
    buf[META_OFF:META_OFF + 8] = b"metadata"
    struct.pack_into("<H", buf, META_OFF + 10, len(items))
    item_off = 32 + len(items) * 32
    for i, item in enumerate(items):
        guid, blob = item[0], item[1]
        flags = item[2] if len(item) > 2 else 0
        reserved = item[3] if len(item) > 3 else 0
        base = META_OFF + 32 + i * 32
        buf[base:base + 16] = guid
        struct.pack_into("<IIII", buf, base + 16, item_off, len(blob), flags, reserved)
        buf[META_OFF + item_off:META_OFF + item_off + len(blob)] = blob
        item_off += len(blob)


def build_vhdx(*, block_size=BLOCK, vds=VDS, flags=0b1, seqs=(1, 2), region_entries=None,
               meta_items=None, bat_entries=None, record=b"") -> bytearray:
    buf = bytearray(FILE_SIZE)
    buf[0:8] = b"vhdxfile"
    _put_header(buf, HEADER_OFFS[0], seqs[0])
    _put_header(buf, HEADER_OFFS[1], seqs[1])
    entries = region_entries if region_entries is not None else [
        (GUID_BAT, BAT_OFF, BAT_LEN, 1), (GUID_METADATA, META_OFF, META_LEN, 1)]
    _put_region_table(buf, REGION_OFFS[0], entries)
    _put_region_table(buf, REGION_OFFS[1], entries)
    items = meta_items if meta_items is not None else [
        (GUID_FILE_PARAMETERS, struct.pack("<II", block_size, flags)),
        (GUID_VIRTUAL_DISK_SIZE, struct.pack("<Q", vds)),
        (GUID_LOGICAL_SECTOR, struct.pack("<I", 512)),
        (GUID_PHYSICAL_SECTOR, struct.pack("<I", 4096)),
        (GUID_VIRTUAL_DISK_ID, b"\x01" * 16),
    ]
    _put_metadata(buf, items)
    count = vds // block_size
    for i in range(count):
        value = ((PAYLOAD_BASE // MB + i) << 20) | 6 if bat_entries is None else bat_entries[i]
        struct.pack_into("<Q", buf, BAT_OFF + i * 8, value)
    if record:
        buf[PAYLOAD_BASE:PAYLOAD_BASE + len(record)] = record
    return buf


def _write(tmp_path: Path, buf: bytearray, name="t.vhdx") -> Path:
    p = tmp_path / name
    p.write_bytes(bytes(buf))
    return p


def _good_record() -> bytes:
    return encode_record({"schema": "f2-s8-synthetic/1", "ok": True, "n": 1})


# --------------------------------------------------------------------------- #
# Record layer
# --------------------------------------------------------------------------- #
class TestRecordFormat:
    def test_round_trip(self):
        payload = {"schema": "f2-s8-synthetic/1", "ok": True, "n": 3}
        blob = encode_record(payload)
        assert blob[:8] == MAGIC
        got, observed = decode_record(blob)
        assert got == payload and observed["total"] == len(blob)

    def test_deterministic(self):
        assert encode_record({"b": 1, "a": 2}) == encode_record({"a": 2, "b": 1})

    @pytest.mark.parametrize("mutate", ["magic", "version", "header", "oversized", "trunc_head",
                                        "trunc_payload", "digest", "trailing", "utf8", "json",
                                        "not_object", "empty"])
    def test_rejections(self, mutate):
        good = bytearray(encode_record({"k": "v"}))
        if mutate == "magic":
            good[0:8] = b"XXXXXXXX"
        elif mutate == "version":
            good[8:10] = struct.pack("<H", 2)
        elif mutate == "header":
            good[10:12] = struct.pack("<H", 40)
        elif mutate == "oversized":
            good[12:16] = struct.pack("<I", MAX_PAYLOAD + 1)
        elif mutate == "trunc_head":
            good = good[:20]
        elif mutate == "trunc_payload":
            good = good[:-1]
        elif mutate == "digest":
            good[16] ^= 0xFF
        elif mutate == "trailing":
            good = good + b"\x00"
        elif mutate in ("utf8", "json", "not_object"):
            body = {"utf8": b"\xff\xfe x", "json": b"{nope", "not_object": b"[1]"}[mutate]
            import hashlib
            good = bytearray(good[:48])
            good[12:16] = struct.pack("<I", len(body))
            good[16:48] = hashlib.sha256(body).digest()
            good = good[:48] + bytearray(body)
        elif mutate == "empty":
            good = b""
        with pytest.raises(RecordError):
            decode_record(bytes(good))


# --------------------------------------------------------------------------- #
# VHDX reader
# --------------------------------------------------------------------------- #
class TestValidFixture:
    def test_valid_minimal_round_trip(self, tmp_path):
        rec = _good_record()
        p = _write(tmp_path, build_vhdx(record=rec))
        payload, observed = read_record_from_vhdx(p)
        assert payload["ok"] is True
        assert observed["record_offset"] == PAYLOAD_BASE
        info = inspect_vhdx(p)
        assert info.layout == "fixed"
        assert info.block_size == BLOCK
        assert info.payload_offset == PAYLOAD_BASE
        assert info.sequence_number == 2          # higher sequence wins
        assert info.region_table_offset == REGION_OFFS[0]
        assert info.virtual_disk_size == VDS

    def test_crc32c_known_vector(self):
        # CRC-32C("123456789") = 0xE3069283 (Castagnoli reference vector)
        assert crc32c(b"123456789") == 0xE3069283

    def test_checksum_field_is_zeroed_for_calculation(self, tmp_path):
        buf = build_vhdx()
        # Recompute the region CRC with the field zeroed; a stored-value hash differs.
        off = REGION_OFFS[0]
        with_field = bytearray(buf[off:off + 64 * 1024])
        declared = struct.unpack_from("<I", with_field, 4)[0]
        assert crc32c(bytes(with_field)) != declared
        zeroed = bytearray(with_field)
        zeroed[4:8] = b"\x00\x00\x00\x00"
        assert crc32c(bytes(zeroed)) == declared


class TestHeaderSelection:
    def test_second_header_invalid_uses_first(self, tmp_path):
        buf = build_vhdx(seqs=(1, 2))
        buf[HEADER_OFFS[1]:HEADER_OFFS[1] + 4] = b"XXXX"
        assert inspect_vhdx(_write(tmp_path, buf)).sequence_number == 1

    def test_first_header_invalid_uses_second(self, tmp_path):
        buf = build_vhdx(seqs=(1, 2))
        buf[HEADER_OFFS[0]:HEADER_OFFS[0] + 4] = b"XXXX"
        assert inspect_vhdx(_write(tmp_path, buf)).sequence_number == 2

    def test_higher_sequence_wins(self, tmp_path):
        buf = build_vhdx(seqs=(7, 3))
        assert inspect_vhdx(_write(tmp_path, buf)).sequence_number == 7

    def test_corrupt_header_crc_falls_back(self, tmp_path):
        buf = build_vhdx(seqs=(1, 2))
        buf[HEADER_OFFS[1] + 4] ^= 0xFF  # corrupt checksum only
        assert inspect_vhdx(_write(tmp_path, buf)).sequence_number == 1

    def test_no_valid_header(self, tmp_path):
        buf = build_vhdx()
        buf[HEADER_OFFS[0]:HEADER_OFFS[0] + 4] = b"XXXX"
        buf[HEADER_OFFS[1]:HEADER_OFFS[1] + 4] = b"XXXX"
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_bad_file_identifier(self, tmp_path):
        buf = build_vhdx()
        buf[0:8] = b"nope!!!!"
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))


class TestRegionTable:
    def test_secondary_copy_recovery(self, tmp_path):
        buf = build_vhdx()
        buf[REGION_OFFS[0] + 4] ^= 0xFF  # corrupt primary CRC
        info = inspect_vhdx(_write(tmp_path, buf))
        assert info.region_table_offset == REGION_OFFS[1]

    def test_bad_signature_both_copies(self, tmp_path):
        buf = build_vhdx()
        for off in REGION_OFFS:
            buf[off:off + 4] = b"XXXX"
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_bad_crc_both_copies(self, tmp_path):
        buf = build_vhdx()
        for off in REGION_OFFS:
            buf[off + 4] ^= 0xFF
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_excessive_entry_count(self, tmp_path):
        buf = build_vhdx()
        for off in REGION_OFFS:
            struct.pack_into("<I", buf, off + 8, 4000)
            _seal_region(buf, off)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_duplicate_guid(self, tmp_path):
        entries = [(GUID_BAT, BAT_OFF, BAT_LEN, 1), (GUID_BAT, META_OFF, META_LEN, 1)]
        buf = build_vhdx(region_entries=entries)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_unknown_required_region(self, tmp_path):
        entries = [(GUID_BAT, BAT_OFF, BAT_LEN, 1), (GUID_METADATA, META_OFF, META_LEN, 1),
                   (bytes.fromhex("00" * 16), 5 * MB, MB, 1)]
        buf = build_vhdx(region_entries=entries)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_unaligned_region(self, tmp_path):
        entries = [(GUID_BAT, BAT_OFF + 512, BAT_LEN, 1), (GUID_METADATA, META_OFF, META_LEN, 1)]
        buf = build_vhdx(region_entries=entries)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_out_of_file_region(self, tmp_path):
        entries = [(GUID_BAT, BAT_OFF, BAT_LEN, 1), (GUID_METADATA, META_OFF, 64 * MB, 1)]
        buf = build_vhdx(region_entries=entries)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_overlapping_regions(self, tmp_path):
        entries = [(GUID_BAT, BAT_OFF, 2 * MB, 1), (GUID_METADATA, BAT_OFF + MB, MB, 1)]
        buf = build_vhdx(region_entries=entries)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_missing_metadata_region(self, tmp_path):
        entries = [(GUID_BAT, BAT_OFF, BAT_LEN, 1)]
        buf = build_vhdx(region_entries=entries)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))


class TestMetadata:
    def _items(self, **over):
        base = {
            "fp": (GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 0b1)),
            "vds": (GUID_VIRTUAL_DISK_SIZE, struct.pack("<Q", VDS)),
            "ls": (GUID_LOGICAL_SECTOR, struct.pack("<I", 512)),
            "ps": (GUID_PHYSICAL_SECTOR, struct.pack("<I", 4096)),
        }
        base.update(over)
        return [v for v in base.values() if v is not None]

    def test_bad_signature(self, tmp_path):
        buf = build_vhdx()
        buf[META_OFF:META_OFF + 8] = b"XXXXXXXX"
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_item_offset_before_table(self, tmp_path):
        buf = build_vhdx()
        base = META_OFF + 32  # first item entry
        struct.pack_into("<I", buf, base + 16, 8)  # offset into the table
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_item_past_region(self, tmp_path):
        buf = build_vhdx()
        base = META_OFF + 32
        struct.pack_into("<I", buf, base + 16, META_LEN - 2)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_duplicate_item_id(self, tmp_path):
        items = [(GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 1)),
                 (GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 1))]
        buf = build_vhdx(meta_items=items)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_missing_file_parameters(self, tmp_path):
        buf = build_vhdx(meta_items=[(GUID_VIRTUAL_DISK_SIZE, struct.pack("<Q", VDS))])
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_truncated_file_parameters(self, tmp_path):
        items = self._items(fp=(GUID_FILE_PARAMETERS, struct.pack("<I", BLOCK)))
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=items)))

    def test_reserved_file_parameter_bits(self, tmp_path):
        items = self._items(fp=(GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 0b100)))
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=items)))

    def test_truncated_virtual_disk_size(self, tmp_path):
        items = self._items(vds=(GUID_VIRTUAL_DISK_SIZE, struct.pack("<I", 0)))
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=items)))

    def test_invalid_sector_size(self, tmp_path):
        items = self._items(ls=(GUID_LOGICAL_SECTOR, struct.pack("<I", 123)))
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=items)))

    def test_invalid_virtual_disk_size(self, tmp_path):
        items = self._items(vds=(GUID_VIRTUAL_DISK_SIZE, struct.pack("<Q", 0)))
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=items)))


class TestBatAndPayload:
    def test_payload_inside_metadata(self, tmp_path):
        buf = build_vhdx()
        struct.pack_into("<Q", buf, BAT_OFF, (META_OFF // MB) << 20 | 6)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_payload_inside_bat(self, tmp_path):
        buf = build_vhdx()
        struct.pack_into("<Q", buf, BAT_OFF, (BAT_OFF // MB) << 20 | 6)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_payload_beyond_file(self, tmp_path):
        buf = build_vhdx()
        struct.pack_into("<Q", buf, BAT_OFF, (40) << 20 | 6)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_invalid_state(self, tmp_path):
        buf = build_vhdx()
        struct.pack_into("<Q", buf, BAT_OFF, (PAYLOAD_BASE // MB) << 20 | 0)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_reserved_bat_bits(self, tmp_path):
        buf = build_vhdx()
        struct.pack_into("<Q", buf, BAT_OFF, (PAYLOAD_BASE // MB) << 20 | 6 | (1 << 10))
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_later_block_not_allocated(self, tmp_path):
        entries = [((PAYLOAD_BASE // MB + i) << 20) | 6 for i in range(BLOCK_COUNT)]
        entries[3] = (PAYLOAD_BASE // MB + 3) << 20 | 0  # not present
        buf = build_vhdx(bat_entries=entries)
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_record_crosses_block_boundary(self, tmp_path):
        # A header declaring MAX_PAYLOAD cannot fit in a 1 MiB block (48 + 1 MiB > 1 MiB).
        buf = build_vhdx()
        struct.pack_into("<8sHHI", buf, PAYLOAD_BASE, MAGIC, 1, HEADER_SIZE, MAX_PAYLOAD)
        with pytest.raises(VhdxError):
            read_record_from_vhdx(_write(tmp_path, buf))

    def test_record_magic_absent(self, tmp_path):
        with pytest.raises(VhdxError):
            read_record_from_vhdx(_write(tmp_path, build_vhdx(record=b"not a record")))

    def test_dynamic_refused(self, tmp_path):
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(flags=0b0)))

    def test_differencing_refused(self, tmp_path):
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(flags=0b11)))

    def test_invalid_block_size(self, tmp_path):
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(block_size=3 * MB)))


class TestBatInterleaving:
    """[MS-VHDX] 2.5: payload and sector-bitmap BAT entries are interleaved."""

    def test_chunk_ratio_formula(self):
        # (2^23 * 512) / 1 MiB == 4096
        assert chunk_ratio_for(MB, 512) == 4096
        assert chunk_ratio_for(MB, 4096) == 32768
        with pytest.raises(VhdxError):
            chunk_ratio_for(3 * MB, 512)  # not an integer ratio

    def test_payload_index_interleaves(self):
        # chunk_ratio 4 -> P P P P SB P P P P SB ... ; payload i -> i + i//4
        assert [bat_payload_index(i, 4) for i in range(10)] == [0, 1, 2, 3, 5, 6, 7, 8, 10, 11]

    def test_sb_index_follows_each_chunk(self):
        assert [bat_sb_index(c, 4, 10) for c in range(3)] == [4, 9, 12]
        # a partial final chunk places the SB entry right after its payloads
        assert bat_sb_index(0, 4096, 8) == 8

    def test_small_disk_is_unchanged_by_interleaving(self):
        # For a small disk (block_count < chunk_ratio) payload index == block index.
        assert [bat_payload_index(i, 4096) for i in range(8)] == list(range(8))


class TestNewInvariants:
    def test_zero_length_required_region_rejected(self, tmp_path):
        entries = [(GUID_BAT, BAT_OFF, 0, 1), (GUID_METADATA, META_OFF, META_LEN, 1)]
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(region_entries=entries)))

    def test_zero_length_entry_does_not_bypass_uniqueness(self, tmp_path):
        entries = [(GUID_BAT, BAT_OFF, BAT_LEN, 1), (GUID_METADATA, META_OFF, META_LEN, 1),
                   (GUID_BAT, 5 * MB, 0, 0)]
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(region_entries=entries)))

    def test_metadata_reserved_field_rejected(self, tmp_path):
        items = [(GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 1), 0, 1),
                 (GUID_VIRTUAL_DISK_SIZE, struct.pack("<Q", VDS)),
                 (GUID_LOGICAL_SECTOR, struct.pack("<I", 512)),
                 (GUID_PHYSICAL_SECTOR, struct.pack("<I", 4096))]
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=items)))

    def test_metadata_reserved_flag_bits_rejected(self, tmp_path):
        items = [(GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 1), 0b1000),
                 (GUID_VIRTUAL_DISK_SIZE, struct.pack("<Q", VDS)),
                 (GUID_LOGICAL_SECTOR, struct.pack("<I", 512)),
                 (GUID_PHYSICAL_SECTOR, struct.pack("<I", 4096))]
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=items)))

    def test_duplicate_item_id_and_isuser_rejected(self, tmp_path):
        items = [(GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 1)),
                 (GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 1)),
                 (GUID_VIRTUAL_DISK_SIZE, struct.pack("<Q", VDS)),
                 (GUID_LOGICAL_SECTOR, struct.pack("<I", 512)),
                 (GUID_PHYSICAL_SECTOR, struct.pack("<I", 4096))]
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=items)))

    def test_zero_length_item_with_nonzero_offset_rejected(self, tmp_path):
        items = [(GUID_VIRTUAL_DISK_SIZE, b""),
                 (GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 1)),
                 (GUID_LOGICAL_SECTOR, struct.pack("<I", 512)),
                 (GUID_PHYSICAL_SECTOR, struct.pack("<I", 4096))]
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=items)))

    def test_equal_sequence_identical_headers_ok(self, tmp_path):
        info = inspect_vhdx(_write(tmp_path, build_vhdx(seqs=(3, 3))))
        assert info.sequence_number == 3

    def test_equal_sequence_differing_headers_rejected(self, tmp_path):
        buf = build_vhdx(seqs=(3, 3))
        buf[HEADER_OFFS[1] + 16] ^= 0xFF  # change one byte of the second copy
        _seal_header(buf, HEADER_OFFS[1])
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))


class TestHeaderLogAndRegionInvariants:
    def test_invalid_header_version(self, tmp_path):
        buf = build_vhdx()
        struct.pack_into("<H", buf, HEADER_OFFS[1] + 66, 2)
        _seal_header(buf, HEADER_OFFS[1])
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_invalid_log_version(self, tmp_path):
        buf = build_vhdx()
        struct.pack_into("<H", buf, HEADER_OFFS[1] + 64, 1)
        _seal_header(buf, HEADER_OFFS[1])
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_reserved_header_byte(self, tmp_path):
        buf = build_vhdx()
        buf[HEADER_OFFS[1] + 200] = 0xFF
        _seal_header(buf, HEADER_OFFS[1])
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_active_log_guid_refused(self, tmp_path):
        buf = build_vhdx()
        buf[HEADER_OFFS[1] + 48] = 0xAA  # non-zero LogGuid
        _seal_header(buf, HEADER_OFFS[1])
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_log_out_of_bounds(self, tmp_path):
        buf = build_vhdx()
        struct.pack_into("<I", buf, HEADER_OFFS[1] + 68, 64 * MB)  # log length beyond file
        _seal_header(buf, HEADER_OFFS[1])
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_valid_empty_log(self, tmp_path):
        # A zero LogGuid with a reserved (nonzero-length, in-bounds) log region is valid.
        buf = build_vhdx()
        struct.pack_into("<I", buf, HEADER_OFFS[1] + 68, MB)
        struct.pack_into("<Q", buf, HEADER_OFFS[1] + 72, MB)
        _seal_header(buf, HEADER_OFFS[1])
        assert inspect_vhdx(_write(tmp_path, buf)).sequence_number == 2

    def test_region_table_reserved_field(self, tmp_path):
        buf = build_vhdx()
        struct.pack_into("<I", buf, REGION_OFFS[0] + 12, 1)
        _seal_region(buf, REGION_OFFS[0])
        buf[REGION_OFFS[1]:REGION_OFFS[1] + 4] = b"XXXX"  # force use of the primary copy
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_invalid_required_value(self, tmp_path):
        entries = [(GUID_BAT, BAT_OFF, BAT_LEN, 0x2), (GUID_METADATA, META_OFF, META_LEN, 1)]
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(region_entries=entries)))

    def test_required_region_guid_colliding_with_metadata_item_rejected(self, tmp_path):
        # A Required REGION whose GUID equals a metadata-item GUID (File Parameters)
        # must NOT be treated as a known region.
        entries = [(GUID_BAT, BAT_OFF, BAT_LEN, 1), (GUID_METADATA, META_OFF, META_LEN, 1),
                   (GUID_FILE_PARAMETERS, 1 * MB, MB, 1)]
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(region_entries=entries)))

    def test_unknown_optional_region_allowed(self, tmp_path):
        # An unknown region that is NOT required is permitted.
        entries = [(GUID_BAT, BAT_OFF, BAT_LEN, 1), (GUID_METADATA, META_OFF, META_LEN, 1),
                   (bytes.fromhex("22" * 16), 1 * MB, MB, 0)]
        info = inspect_vhdx(_write(tmp_path, build_vhdx(region_entries=entries)))
        assert info.layout == "fixed"


class TestMetadataInvariants:
    def _items(self, extra=(), vdisk_id=b"\x01" * 16):
        items = [
            (GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 1)),
            (GUID_VIRTUAL_DISK_SIZE, struct.pack("<Q", VDS)),
            (GUID_LOGICAL_SECTOR, struct.pack("<I", 512)),
            (GUID_PHYSICAL_SECTOR, struct.pack("<I", 4096)),
            (GUID_VIRTUAL_DISK_ID, vdisk_id),
        ]
        return items + list(extra)

    def test_missing_virtual_disk_id(self, tmp_path):
        items = [(GUID_FILE_PARAMETERS, struct.pack("<II", BLOCK, 1)),
                 (GUID_VIRTUAL_DISK_SIZE, struct.pack("<Q", VDS)),
                 (GUID_LOGICAL_SECTOR, struct.pack("<I", 512)),
                 (GUID_PHYSICAL_SECTOR, struct.pack("<I", 4096))]
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=items)))

    def test_wrong_virtual_disk_id_length(self, tmp_path):
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=self._items(vdisk_id=b"\x01" * 8))))

    def test_metadata_header_reserved_field(self, tmp_path):
        buf = build_vhdx()
        buf[META_OFF + 8] = 0x01
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_unknown_required_metadata_rejected(self, tmp_path):
        unknown = (bytes.fromhex("11" * 16), b"x" * 4, 0b100)  # IsRequired
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=self._items(extra=[unknown]))))

    def test_unknown_optional_metadata_allowed(self, tmp_path):
        unknown = (bytes.fromhex("11" * 16), b"x" * 4, 0)  # optional
        info = inspect_vhdx(_write(tmp_path, build_vhdx(meta_items=self._items(extra=[unknown]))))
        assert info.layout == "fixed"


class TestOverlapDefects:
    def test_log_overlaps_metadata_region_rejected(self, tmp_path):
        buf = build_vhdx()
        struct.pack_into("<I", buf, HEADER_OFFS[1] + 68, MB)     # LogLength = 1 MiB
        struct.pack_into("<Q", buf, HEADER_OFFS[1] + 72, 2 * MB)  # LogOffset == metadata region
        _seal_header(buf, HEADER_OFFS[1])
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_partial_payload_overlap_rejected(self, tmp_path):
        # block_size 2 MiB -> blocks at 4,5,6,7 MiB partially overlap.
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, build_vhdx(block_size=2 * MB)))

    def test_duplicate_payload_start_rejected(self, tmp_path):
        buf = build_vhdx()
        first = struct.unpack_from("<Q", buf, BAT_OFF)[0]
        struct.pack_into("<Q", buf, BAT_OFF + 8, first)  # block 1 aliases block 0
        with pytest.raises(VhdxError):
            inspect_vhdx(_write(tmp_path, buf))

    def test_valid_adjacent_payload_blocks_accepted(self, tmp_path):
        info = inspect_vhdx(_write(tmp_path, build_vhdx(record=_good_record())))
        assert info.block_size == BLOCK

    def test_valid_gap_between_payload_blocks_accepted(self, tmp_path):
        buf = build_vhdx(vds=4 * MB)  # 4 blocks: default offsets 4,5,6,7 MiB
        for i, off_mb in ((1, 6), (2, 7), (3, 8)):
            struct.pack_into("<Q", buf, BAT_OFF + i * 8, (off_mb << 20) | 6)
        # spans [4,5) [6,7) [7,8) [8,9): disjoint, with a gap between the first two
        info = inspect_vhdx(_write(tmp_path, buf))
        assert info.payload_offset == PAYLOAD_BASE


class TestReaderMechanics:
    def test_reader_source_has_no_attach_or_subprocess(self):
        """Supplemental static guard: the reader must not attach/mount or shell out."""
        import qwen_train.f2_s8_vhdx as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        for banned in ("Mount-VHD", "New-VHD", "Dismount-VHD", "subprocess", "os.system"):
            assert banned not in src

    def test_reader_opens_read_only(self, tmp_path, monkeypatch):
        """Behavioral guard: the reader opens the file with mode 'rb' and never writes."""
        import qwen_train.f2_s8_vhdx as mod

        p = _write(tmp_path, build_vhdx(record=_good_record()))  # build before patching
        seen = []
        real_open = Path.open

        def spy(self, *a, **k):
            seen.append(a[0] if a else k.get("mode", "r"))
            return real_open(self, *a, **k)

        monkeypatch.setattr(Path, "open", spy)
        mod.read_record_from_vhdx(p)
        assert seen and all(mode == "rb" for mode in seen)

    @pytest.mark.skipif(not SCRATCH_VHDX.exists(), reason="scratch VHDX not present")
    def test_scratch_vhdx_still_reads(self):
        """Supplementary: the earlier scratch fixture still round-trips."""
        payload, observed = read_record_from_vhdx(SCRATCH_VHDX)
        assert payload["payload"] == "hello-run1"
        assert observed["record_offset"] == observed["container"]["payload_offset"]
