"""Direct, unattached reader for a FIXED VHDX containing one bounded record.

Stage 1 / Run 1, hardened after the post-Run-1 adversarial audit (F1-F9).

Design basis: Microsoft [MS-VHDX] "Virtual Hard Disk (VHDX) File Format":
  * 2.1 Layout - the two region-table copies live at FIXED offsets 192 KiB and
        256 KiB; the header section occupies [0, 192 KiB).
  * 2.2.2 Headers - two copies at 64 KiB and 128 KiB; each has Signature
        "head", a CRC-32C Checksum over the 4 KiB block, and a SequenceNumber.
        The current header is the valid copy with the higher sequence number.
  * 2.2.3.1/2.2.3.2 Region Table - Signature "regi", a CRC-32C Checksum over the
        64 KiB copy (computed with the checksum field zeroed), EntryCount, and
        32-byte entries {Guid, FileOffset (1 MiB units), Length (1 MiB units),
        Required}.
  * 2.6 Metadata Region - Signature "metadata"; 32-byte entries {ItemId,
        Offset, Length, IsUser, Reserved}; item offsets/lengths are relative to
        the region start and MUST lie inside it.
  * 2.6.2.1 File Parameters - BlockSize (uint32) + Flags (uint32); bit 0 =
        LeaveBlocksAllocated, bit 1 = HasParent; other bits reserved (0).
  * 2.6.2.2 Virtual Disk Size - uint64.
  * 2.5.1 BAT Entry - bits 0-2 State (PAYLOAD_BLOCK_FULLY_PRESENT = 6),
        bits 3-19 Reserved (MUST be 0), bits 20-63 FileOffsetMB (1 MiB units).

Supported: FIXED VHDX only.  Dynamic and differencing VHDX are refused.  The
reader parses only container metadata plus one bounded record; it never attaches
or mounts the disk and never asks Windows to parse its payload as a filesystem.

SAFETY LIMITATION: **READER MECHANICS ONLY; A HOSTILE GUEST-WRITTEN DISK IS NOT
TESTED.**  A valid record digest proves the *record bytes* match their hash.  It
does NOT prove the container, the guest, or the evidence source is trustworthy,
and it is not container authenticity or scientific provenance.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qwen_train.f2_s8_record import HEADER_SIZE, MAGIC, MAX_PAYLOAD, RecordError, decode_record

__all__ = ["VhdxError", "VhdxInfo", "inspect_vhdx", "read_record_from_vhdx", "crc32c",
           "chunk_ratio_for", "bat_payload_index", "bat_sb_index"]

HEADER_OFFSETS = (64 * 1024, 128 * 1024)
REGION_TABLE_OFFSETS = (192 * 1024, 256 * 1024)
HEADER_SECTION_END = 192 * 1024
REGION_TABLE_END = 320 * 1024
MB = 1024 * 1024
PAYLOAD_BLOCK_FULLY_PRESENT = 6
SB_BLOCK_NOT_PRESENT = 0
SB_BLOCK_PRESENT = 6
CHUNK_BASE = 1 << 23  # [MS-VHDX] 2.5: chunk ratio uses 2^23 * logical sector size
MAX_BLOCKS = 1 << 16  # bound on BAT entries we will inspect

# On-disk GUID byte order (Data1/2/3 little-endian, Data4 big-endian).
GUID_BAT = bytes.fromhex("6677c22d23f600429d64115e9bfd4a08")
GUID_METADATA = bytes.fromhex("06a27c8b90479a4bb8fe575f050f886e")
GUID_FILE_PARAMETERS = bytes.fromhex("3767a1ca36fa434db3b633f0aa44e76b")
GUID_VIRTUAL_DISK_SIZE = bytes.fromhex("2442a52f1bcd7648b2115dbed83bf4b8")
GUID_LOGICAL_SECTOR = bytes.fromhex("1dbf41816fa90947ba47f233a8faab5f")
GUID_PHYSICAL_SECTOR = bytes.fromhex("c748a3cd5d4471449cc9e9885251c556")
GUID_VIRTUAL_DISK_ID = bytes.fromhex("ab12cabee6b2234593efc309e000c746")  # {BECA12AB-B2E6-4523-93EF-C309E000C746}
GUID_KNOWN = {GUID_BAT, GUID_METADATA, GUID_FILE_PARAMETERS, GUID_VIRTUAL_DISK_SIZE,
              GUID_LOGICAL_SECTOR, GUID_PHYSICAL_SECTOR, GUID_VIRTUAL_DISK_ID}
# Metadata entry flag bits ([MS-VHDX] 2.6.1).
META_FLAG_ISUSER = 0b1
META_FLAG_ISVIRTUALDISK = 0b10
META_FLAG_ISREQUIRED = 0b100
KNOWN_METADATA_ITEMS = {GUID_FILE_PARAMETERS, GUID_VIRTUAL_DISK_SIZE, GUID_LOGICAL_SECTOR,
                        GUID_PHYSICAL_SECTOR, GUID_VIRTUAL_DISK_ID}
REQUIRED_FLAG = 0x1


class VhdxError(ValueError):
    """The VHDX container is malformed, unsupported, or fails an invariant."""


def chunk_ratio_for(block_size: int, logical_sector_size: int) -> int:
    """[MS-VHDX] 2.5: ChunkRatio = (2^23 * LogicalSectorSize) / BlockSize."""
    span = CHUNK_BASE * logical_sector_size
    if span % block_size != 0:
        raise VhdxError("chunk ratio is not an integer for this geometry")
    ratio = span // block_size
    if ratio < 1:
        raise VhdxError(f"invalid chunk ratio {ratio}")
    return ratio


def bat_payload_index(block: int, chunk_ratio: int) -> int:
    """BAT index of payload block ``block`` (payload/SB entries are interleaved)."""
    chunk, within = divmod(block, chunk_ratio)
    return chunk * (chunk_ratio + 1) + within


def bat_sb_index(chunk: int, chunk_ratio: int, block_count: int) -> int:
    """BAT index of the sector-bitmap entry that follows payload ``chunk``."""
    payloads_in_chunk = min(chunk_ratio, block_count - chunk * chunk_ratio)
    return chunk * (chunk_ratio + 1) + payloads_in_chunk


def crc32c(data: bytes) -> int:
    """CRC-32C (Castagnoli), as used by the VHDX header/region checksums."""
    poly = 0x82F63B78
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


@dataclass(frozen=True)
class VhdxInfo:
    block_size: int
    leave_blocks_allocated: bool
    has_parent: bool
    virtual_disk_size: int
    logical_sector_size: int
    physical_sector_size: int
    bat_offset: int
    bat_length: int
    bat_entry0: int
    payload_offset: int
    payload_block_size: int
    file_size: int
    header_offset: int
    sequence_number: int
    region_table_offset: int
    layout: str = "fixed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "layout": self.layout,
            "block_size": self.block_size,
            "leave_blocks_allocated": self.leave_blocks_allocated,
            "has_parent": self.has_parent,
            "virtual_disk_size": self.virtual_disk_size,
            "logical_sector_size": self.logical_sector_size,
            "physical_sector_size": self.physical_sector_size,
            "bat_offset": self.bat_offset,
            "bat_length": self.bat_length,
            "bat_entry0": self.bat_entry0,
            "payload_offset": self.payload_offset,
            "payload_block_size": self.payload_block_size,
            "file_size": self.file_size,
            "header_offset": self.header_offset,
            "sequence_number": self.sequence_number,
            "region_table_offset": self.region_table_offset,
        }


def _read_exact(fh, offset: int, size: int, file_size: int, what: str) -> bytes:
    if offset < 0 or size < 0 or offset + size > file_size:
        raise VhdxError(f"{what}: out-of-bounds read ({offset}+{size} > {file_size})")
    fh.seek(offset)
    data = fh.read(size)
    if len(data) != size:
        raise VhdxError(f"{what}: short read")
    return data


def _unpack(fmt: str, blob: bytes, what: str):
    try:
        return struct.unpack_from(fmt, blob, 0)
    except struct.error as exc:
        raise VhdxError(f"{what}: truncated structure ({exc})") from exc


def _parse_header(raw: bytes, file_size: int, off: int):
    """Return (sequence_number) for a valid header block, else None."""
    if len(raw) != 4096 or raw[:4] != b"head":
        return None
    declared = struct.unpack_from("<I", raw, 4)[0]
    body = bytearray(raw)
    body[4:8] = b"\x00\x00\x00\x00"
    if crc32c(bytes(body)) != declared:
        return None
    (seq,) = struct.unpack_from("<Q", raw, 8)
    return seq


def _parse_region_table(raw: bytes, file_size: int, off: int):
    """Validate a 64 KiB region-table copy; return its entries or None."""
    if len(raw) != 64 * 1024 or raw[:4] != b"regi":
        return None
    declared = struct.unpack_from("<I", raw, 4)[0]
    body = bytearray(raw)
    body[4:8] = b"\x00\x00\x00\x00"
    if crc32c(bytes(body)) != declared:
        return None
    (entry_count,) = struct.unpack_from("<I", raw, 8)
    (rt_reserved,) = struct.unpack_from("<I", raw, 12)
    if rt_reserved != 0:
        raise VhdxError("region table reserved field is nonzero")
    if not 1 <= entry_count <= 2047:
        raise VhdxError(f"implausible region entry count {entry_count}")
    entries = []
    seen_guids: set[bytes] = set()
    for i in range(entry_count):
        base = 16 + i * 32
        guid = bytes(raw[base:base + 16])
        (file_offset, length, required) = struct.unpack_from("<QII", raw, base + 16)
        if required & ~REQUIRED_FLAG:
            raise VhdxError(f"region {i}: invalid Required value {required:#x} (reserved bits set)")
        if guid in seen_guids:
            raise VhdxError(f"duplicate region GUID at index {i}")
        seen_guids.add(guid)
        if length == 0:
            # A zero-length entry MUST NOT bypass uniqueness/required checks.
            if guid in (GUID_BAT, GUID_METADATA) or (required & REQUIRED_FLAG):
                raise VhdxError(f"region {i}: required region has zero length")
            continue
        if file_offset % MB != 0 or length % MB != 0:
            raise VhdxError(f"region {i}: offset/length not 1 MiB aligned")
        if file_offset + length > file_size:
            raise VhdxError(f"region {i}: exceeds file size")
        entries.append({"guid": guid, "offset": file_offset, "length": length,
                        "required": bool(required & REQUIRED_FLAG), "index": i})
    return entries


def _check_regions(entries, file_size: int):
    seen = set()
    for e in entries:
        if e["guid"] in seen:
            raise VhdxError(f"duplicate region GUID at index {e['index']}")
        seen.add(e["guid"])
        if e["required"] and e["guid"] not in GUID_KNOWN:
            raise VhdxError(f"unknown REQUIRED region at index {e['index']}")
    # overlaps between declared regions
    ordered = sorted(entries, key=lambda e: e["offset"])
    for a, b in zip(ordered, ordered[1:]):
        if a["offset"] + a["length"] > b["offset"]:
            raise VhdxError(f"regions overlap: {a['index']} and {b['index']}")


def _structural_ranges(entries, log_offset: int, log_length: int):
    ranges = [(0, HEADER_SECTION_END), (HEADER_SECTION_END, REGION_TABLE_END)]
    for e in entries:
        ranges.append((e["offset"], e["offset"] + e["length"]))
    if log_length:
        ranges.append((log_offset, log_offset + log_length))
    return ranges


def inspect_vhdx(path: Path) -> VhdxInfo:
    """Validate the container structure and compute the payload block location."""
    path = Path(path)
    file_size = path.stat().st_size
    if file_size < REGION_TABLE_END:
        raise VhdxError(f"file too small to be a VHDX: {file_size}")

    with path.open("rb") as fh:
        if _read_exact(fh, 0, 8, file_size, "file identifier") != b"vhdxfile":
            raise VhdxError("bad file identifier (expected 'vhdxfile')")

        # --- Headers: validate both copies, select the current one by sequence ---
        valid = []
        for off in HEADER_OFFSETS:
            seq = _parse_header(_read_exact(fh, off, 4096, file_size, f"header@{off}"), file_size, off)
            if seq is not None:
                valid.append((seq, off))
        if not valid:
            raise VhdxError("no valid VHDX header found (signature/checksum)")
        seq, header_offset = max(valid, key=lambda t: (t[0], -t[1]))
        if len(valid) == 2 and valid[0][0] == valid[1][0]:
            # Equal sequence numbers: the two copies must be identical; a
            # disagreement means the file is inconsistent and must be refused.
            a = _read_exact(fh, valid[0][1], 4096, file_size, "header copy")
            b = _read_exact(fh, valid[1][1], 4096, file_size, "header copy")
            if a != b:
                raise VhdxError("two valid headers have equal sequence numbers but differ")
        header = _read_exact(fh, header_offset, 4096, file_size, "current header")
        (log_version,) = struct.unpack_from("<H", header, 64)
        (version,) = struct.unpack_from("<H", header, 66)
        (log_length,) = struct.unpack_from("<I", header, 68)
        (log_offset,) = struct.unpack_from("<Q", header, 72)
        log_guid = header[48:64]
        if version != 1:
            raise VhdxError(f"unsupported VHDX header version {version}")
        if log_version != 0:
            raise VhdxError(f"unsupported VHDX log version {log_version}")
        if any(header[80:4096]):
            raise VhdxError("header reserved bytes are not zero")
        if log_guid != b"\x00" * 16:
            # A non-zero LogGuid means the log may require replay, which this
            # reader does not implement; refuse rather than mis-parse.
            raise VhdxError("VHDX log is active (LogGuid set); log replay is not supported")
        if log_length:
            if log_length % MB != 0 or log_offset % MB != 0 or log_offset + log_length > file_size:
                raise VhdxError("invalid log region in header")
        elif log_offset != 0:
            raise VhdxError("log_offset is nonzero but log_length is zero")

        # --- Region table: two fixed copies; use the valid one ---
        entries = None
        region_table_offset = None
        for off in REGION_TABLE_OFFSETS:
            candidate = _parse_region_table(
                _read_exact(fh, off, 64 * 1024, file_size, f"region table@{off}"), file_size, off
            )
            if candidate is not None:
                entries, region_table_offset = candidate, off
                break
        if entries is None:
            raise VhdxError("no valid region table found (signature/checksum)")
        _check_regions(entries, file_size)

        bat = next((e for e in entries if e["guid"] == GUID_BAT), None)
        metadata = next((e for e in entries if e["guid"] == GUID_METADATA), None)
        if bat is None or metadata is None:
            raise VhdxError("BAT or metadata region missing")
        if not bat["required"] or not metadata["required"]:
            raise VhdxError("BAT/metadata region not marked required")

        # --- Metadata region ---
        meta_hdr = _read_exact(fh, metadata["offset"], 32, file_size, "metadata header")
        if meta_hdr[:8] != b"metadata":
            raise VhdxError("bad metadata signature")
        if meta_hdr[8:10] != b"\x00\x00":
            raise VhdxError("metadata header reserved field is nonzero")
        if any(meta_hdr[12:32]):
            raise VhdxError("metadata header trailing reserved bytes are nonzero")
        (meta_count,) = _unpack("<H", meta_hdr[10:12], "metadata entry count")
        if not 1 <= meta_count <= 2047:
            raise VhdxError(f"implausible metadata entry count {meta_count}")
        if 32 + meta_count * 32 > metadata["length"]:
            raise VhdxError("metadata table does not fit in the metadata region")
        meta_entries = _read_exact(fh, metadata["offset"] + 32, meta_count * 32, file_size, "metadata entries")
        fields: dict[bytes, bytes] = {}
        seen_items: set[tuple[bytes, bool]] = set()
        spans = []
        for i in range(meta_count):
            base = i * 32
            guid = bytes(meta_entries[base:base + 16])
            (item_offset, item_length, flags, reserved) = struct.unpack_from("<IIII", meta_entries, base + 16)
            if reserved != 0:
                raise VhdxError(f"metadata item {i} has a nonzero reserved field")
            if flags & ~0b111:
                raise VhdxError(f"metadata item {i} has reserved flag bits set ({flags:#x})")
            is_user = bool(flags & META_FLAG_ISUSER)
            if (flags & META_FLAG_ISREQUIRED) and guid not in KNOWN_METADATA_ITEMS:
                raise VhdxError(f"metadata item {i} is unknown but marked required")
            key = (guid, is_user)
            if key in seen_items:
                raise VhdxError(f"duplicate (ItemId, IsUser) metadata entry at index {i}")
            seen_items.add(key)
            if item_length == 0:
                if item_offset != 0:
                    raise VhdxError(f"metadata item {i} is zero-length but has a nonzero offset")
                continue
            if item_length > 1 * MB:
                raise VhdxError(f"metadata item {i} length {item_length} too large")
            if item_offset < 32 + meta_count * 32:
                raise VhdxError(f"metadata item {i} overlaps the table")
            if item_offset + item_length > metadata["length"]:
                raise VhdxError(f"metadata item {i} extends past the metadata region")
            spans.append((item_offset, item_offset + item_length, i))
            if not is_user:
                fields[guid] = _read_exact(
                    fh, metadata["offset"] + item_offset, item_length, file_size, f"metadata item {i}"
                )
        spans.sort()
        for a, b in zip(spans, spans[1:]):
            if a[1] > b[0]:
                raise VhdxError(f"metadata items overlap: {a[2]} and {b[2]}")

        # --- File Parameters / geometry ---
        if GUID_VIRTUAL_DISK_ID not in fields or len(fields[GUID_VIRTUAL_DISK_ID]) != 16:
            raise VhdxError("Virtual Disk ID metadata missing or wrong length (expected 16 bytes)")
        if GUID_FILE_PARAMETERS not in fields or len(fields[GUID_FILE_PARAMETERS]) != 8:
            raise VhdxError("File Parameters missing or wrong length (expected 8 bytes)")
        block_size, flags = struct.unpack_from("<II", fields[GUID_FILE_PARAMETERS], 0)
        if flags & ~0b11:
            raise VhdxError(f"File Parameters reserved bits set: {flags:#x}")
        leave_blocks_allocated = bool(flags & 0b1)
        has_parent = bool(flags & 0b10)
        if has_parent:
            raise VhdxError("differencing VHDX (HasParent) is not supported")
        if not leave_blocks_allocated:
            raise VhdxError("dynamic VHDX (LeaveBlocksAllocated=0) is not supported")
        if block_size & (block_size - 1) != 0 or not (1 * MB <= block_size <= 256 * MB):
            raise VhdxError(f"invalid block size {block_size}")

        if GUID_VIRTUAL_DISK_SIZE not in fields or len(fields[GUID_VIRTUAL_DISK_SIZE]) != 8:
            raise VhdxError("Virtual Disk Size missing or wrong length")
        (virtual_disk_size,) = struct.unpack_from("<Q", fields[GUID_VIRTUAL_DISK_SIZE], 0)
        if virtual_disk_size <= 0 or virtual_disk_size % MB != 0:
            raise VhdxError(f"invalid virtual disk size {virtual_disk_size}")

        def _sector(guid, name):
            if guid not in fields or len(fields[guid]) != 4:
                raise VhdxError(f"{name} missing or wrong length")
            (v,) = struct.unpack_from("<I", fields[guid], 0)
            if v not in (512, 4096):
                raise VhdxError(f"unsupported {name} {v}")
            return v

        logical_sector = _sector(GUID_LOGICAL_SECTOR, "logical sector size")
        physical_sector = _sector(GUID_PHYSICAL_SECTOR, "physical sector size")
        if logical_sector > physical_sector:
            raise VhdxError("logical sector size exceeds physical sector size")

        # --- BAT geometry: payload and sector-bitmap entries are INTERLEAVED ---
        # [MS-VHDX] 2.5: ChunkRatio = (2^23 * LogicalSectorSize) / BlockSize.  A
        # chunk holds ChunkRatio payload entries followed by ONE sector-bitmap
        # entry, so payload block i is NOT at BAT index i.  Fixed VHDX never
        # allocates sector-bitmap blocks, so every SB entry MUST be
        # SB_BLOCK_NOT_PRESENT (0).
        block_count = virtual_disk_size // block_size
        if not 1 <= block_count <= MAX_BLOCKS:
            raise VhdxError(f"implausible block count {block_count}")
        chunk_ratio = chunk_ratio_for(block_size, logical_sector)
        num_chunks = (block_count + chunk_ratio - 1) // chunk_ratio
        total_entries = block_count + num_chunks
        required_bat_bytes = total_entries * 8
        if bat["length"] < required_bat_bytes:
            raise VhdxError(f"BAT region too small ({bat['length']} < {required_bat_bytes})")
        bat_entries = _read_exact(fh, bat["offset"], required_bat_bytes, file_size, "BAT entries")

        def payload_index(block: int) -> int:
            return bat_payload_index(block, chunk_ratio)

        def sb_index(chunk: int) -> int:
            return bat_sb_index(chunk, chunk_ratio, block_count)

        structural = _structural_ranges(entries, log_offset, log_length)
        payload_offset = None
        bat0 = 0
        seen_offsets: set[int] = set()
        for i in range(block_count):
            idx = payload_index(i)
            (entry,) = struct.unpack_from("<Q", bat_entries, idx * 8)
            state = entry & 0x7
            if (entry >> 3) & 0x1FFFF:
                raise VhdxError(f"BAT payload entry {i} (index {idx}) has non-zero reserved bits")
            if state != PAYLOAD_BLOCK_FULLY_PRESENT:
                raise VhdxError(
                    f"BAT payload entry {i} state {state} is not FULLY_PRESENT "
                    "(a fixed disk must allocate every block)"
                )
            block_off = (entry >> 20) * MB
            if block_off in seen_offsets:
                raise VhdxError(f"BAT payload entry {i} aliases an earlier block offset")
            seen_offsets.add(block_off)
            if block_off + block_size > file_size:
                raise VhdxError(f"BAT payload entry {i} block extends beyond the file")
            if block_off < REGION_TABLE_END:
                raise VhdxError(f"BAT payload entry {i} block overlaps the header/region-table section")
            for r0, r1 in structural:
                if block_off < r1 and r0 < block_off + block_size:
                    raise VhdxError(f"BAT payload entry {i} block overlaps a structural region [{r0},{r1})")
            if i == 0:
                bat0, payload_offset = entry, block_off
        for chunk in range(num_chunks):
            idx = sb_index(chunk)
            (entry,) = struct.unpack_from("<Q", bat_entries, idx * 8)
            if (entry >> 3) & 0x1FFFF:
                raise VhdxError(f"sector-bitmap entry {idx} has non-zero reserved bits")
            if (entry & 0x7) != SB_BLOCK_NOT_PRESENT:
                raise VhdxError(
                    f"sector-bitmap entry {idx} state {entry & 0x7} is not NOT_PRESENT "
                    "(fixed VHDX must not allocate sector-bitmap blocks)"
                )
            if entry != 0:
                raise VhdxError(f"sector-bitmap entry {idx} carries a nonzero offset")

        # virtual disk size must be addressable by the located blocks
        if payload_offset + block_size > file_size:
            raise VhdxError("payload block extends beyond the file")

    return VhdxInfo(
        block_size=block_size,
        leave_blocks_allocated=leave_blocks_allocated,
        has_parent=has_parent,
        virtual_disk_size=virtual_disk_size,
        logical_sector_size=logical_sector,
        physical_sector_size=physical_sector,
        bat_offset=bat["offset"],
        bat_length=bat["length"],
        bat_entry0=bat0,
        payload_offset=payload_offset,
        payload_block_size=block_size,
        file_size=file_size,
        header_offset=header_offset,
        sequence_number=seq,
        region_table_offset=region_table_offset,
    )


def read_record_from_vhdx(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the container, read one bounded record, and return ``(payload, observed)``."""
    info = inspect_vhdx(path)
    file_size = info.file_size
    with Path(path).open("rb") as fh:
        header = _read_exact(fh, info.payload_offset, HEADER_SIZE, file_size, "record header")
        if header[:8] != MAGIC:
            raise VhdxError("record magic not found at the validated payload offset")
        payload_len = struct.unpack_from("<I", header, 12)[0]
        if payload_len > MAX_PAYLOAD:
            raise VhdxError(f"record declares oversized payload {payload_len}")
        total = HEADER_SIZE + payload_len
        if total > info.payload_block_size:
            raise VhdxError(f"record ({total} bytes) does not fit in one {info.payload_block_size}-byte block")
        if info.payload_offset + total > file_size:
            raise VhdxError("record extends beyond end of file")
        blob = _read_exact(fh, info.payload_offset, total, file_size, "record")
    try:
        payload, observed = decode_record(blob, require_exact=True)
    except RecordError as exc:
        raise VhdxError(f"record decode failed: {exc}") from exc
    observed = {
        **observed,
        "container": info.to_dict(),
        "record_offset": info.payload_offset,
        "bytes_read": total,
    }
    return payload, observed
