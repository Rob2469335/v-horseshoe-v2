"""Direct, unattached reader for a FIXED VHDX containing one bounded record.

Stage 1 / Run 1.  This is the leading candidate ("Route B2") for recovering a
guest-written result without ever attaching the virtual disk and without letting
Windows recognise, mount, or parse any guest filesystem.

Design basis: Microsoft [MS-VHDX] "Virtual Hard Disk (VHDX) File Format".
  * 2.1 File Identifier ("vhdxfile" at file offset 0)
  * 2.2 Header (signature "head"; CRC32C over the 4 KiB header block)
  * 2.3 Region Table (signature "regi"; BAT + Metadata region GUIDs)
  * 2.4 Metadata Region (signature "metadata"; File Parameters / Virtual Disk
        Size / Logical + Physical Sector Size entries)
  * 2.5 BAT / payload blocks (uint64 entry; bits 0..19 = offset in MiB,
        bit 63 = state, 1 = PAYLOAD_BLOCK_FULLY_PRESENT)
  * 2.7 Fixed VHDX (BlockSize = 1 MiB, LeaveBlocksAllocated = 1, HasParent = 0)

What this reader does NOT do
----------------------------
* It never attaches the VHDX, never mounts it, and never asks Windows to parse
  its payload as a filesystem.
* It parses only container metadata plus one bounded record.
* It does NOT prove a hostile guest-written disk is safe.  Record integrity is
  not container/guest safety.  A guest with write access controls the payload
  bytes and *can* alter container metadata; every field read here is bounds- and
  invariant-checked, but the file is still untrusted input.

Supported: FIXED VHDX only, BlockSize 1 MiB, record at the first payload block.
Dynamic and differencing VHDX are refused.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qwen_train.f2_s8_record import HEADER_SIZE, MAGIC, MAX_PAYLOAD, RecordError, decode_record

__all__ = ["VhdxError", "VhdxInfo", "inspect_vhdx", "read_record_from_vhdx", "crc32c"]

HEADER_OFFSETS = (64 * 1024, 128 * 1024)
REGION_TABLE_OFFSET = 192 * 1024
MB = 1024 * 1024
PAYLOAD_BLOCK_FULLY_PRESENT = 6

# On-disk GUID byte order (Data1/2/3 little-endian, Data4 big-endian), per [MS-VHDX] 2.3/2.4.
GUID_BAT = bytes.fromhex("6677c22d23f600429d64115e9bfd4a08")            # {2DC27766-F623-4200-9D64-115E9BFD4A08}
GUID_METADATA = bytes.fromhex("06a27c8b90479a4bb8fe575f050f886e")       # {8B7CA206-4790-4B9A-B8FE-575F050F886E}
GUID_FILE_PARAMETERS = bytes.fromhex("3767a1ca36fa434db3b633f0aa44e76b")  # {CAA16737-FA36-4D43-B3B6-33F0AA44E76B}
GUID_VIRTUAL_DISK_SIZE = bytes.fromhex("2442a52f1bcd7648b2115dbed83bf4b8")  # {2FA54224-CD1B-4876-B211-5DBED83BF4B8}
GUID_LOGICAL_SECTOR = bytes.fromhex("1dbf41816fa90947ba47f233a8faab5f")   # {8141BF1D-A96F-4709-BA47-F233A8FAAB5F}
GUID_PHYSICAL_SECTOR = bytes.fromhex("c748a3cd5d4471449cc9e9885251c556")  # {CDA348C7-445D-4471-9CC9-E9885251C556}


class VhdxError(ValueError):
    """The VHDX container is malformed, unsupported, or fails an invariant."""


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
    bat_entry0: int
    payload_offset: int
    file_size: int
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
            "bat_entry0": self.bat_entry0,
            "payload_offset": self.payload_offset,
            "file_size": self.file_size,
        }





def _read_exact(fh, offset: int, size: int, file_size: int, what: str) -> bytes:
    if offset < 0 or size < 0 or offset + size > file_size:
        raise VhdxError(f"{what}: out-of-bounds read ({offset}+{size} > {file_size})")
    fh.seek(offset)
    data = fh.read(size)
    if len(data) != size:
        raise VhdxError(f"{what}: short read")
    return data


def inspect_vhdx(path: Path) -> VhdxInfo:
    """Parse the minimum container metadata and compute the payload offset."""
    path = Path(path)
    file_size = path.stat().st_size
    if file_size < REGION_TABLE_OFFSET + 64 * 1024:
        raise VhdxError(f"file too small to be a VHDX: {file_size}")

    with path.open("rb") as fh:
        ident = _read_exact(fh, 0, 8, file_size, "file identifier")
        if ident != b"vhdxfile":
            raise VhdxError("bad file identifier (expected 'vhdxfile')")

        # --- Header (validate the first usable header: signature + CRC32C) ---
        header = None
        for off in HEADER_OFFSETS:
            raw = bytearray(_read_exact(fh, off, 4096, file_size, f"header@{off}"))
            if raw[:4] != b"head":
                continue
            declared = struct.unpack_from("<I", raw, 4)[0]
            raw[4:8] = b"\x00\x00\x00\x00"
            if crc32c(bytes(raw)) != declared:
                continue
            header = off
            break
        if header is None:
            raise VhdxError("no valid VHDX header found (signature/checksum)")

        # --- Region table ---
        regi = _read_exact(fh, REGION_TABLE_OFFSET, 64, file_size, "region table header")
        if regi[:4] != b"regi":
            raise VhdxError("bad region table signature")
        entry_count = struct.unpack_from("<I", regi, 8)[0]
        if not 1 <= entry_count <= 2047:
            raise VhdxError(f"implausible region entry count {entry_count}")
        entries = _read_exact(fh, REGION_TABLE_OFFSET + 16, entry_count * 32, file_size, "region entries")
        bat_offset = metadata_offset = None
        for i in range(entry_count):
            guid = entries[i * 32 : i * 32 + 16]
            file_offset, length, _required = struct.unpack_from("<QII", entries, i * 32 + 16)
            if length == 0:
                continue
            if file_offset + length > file_size:
                raise VhdxError(f"region {i} exceeds file size")
            if guid == GUID_BAT:
                bat_offset = file_offset
            elif guid == GUID_METADATA:
                metadata_offset = file_offset
        if bat_offset is None or metadata_offset is None:
            raise VhdxError("BAT or metadata region missing")

        # --- Metadata region ---
        meta_hdr = _read_exact(fh, metadata_offset, 32, file_size, "metadata header")
        if meta_hdr[:8] != b"metadata":
            raise VhdxError("bad metadata signature")
        meta_count = struct.unpack_from("<H", meta_hdr, 10)[0]
        if not 1 <= meta_count <= 2047:
            raise VhdxError(f"implausible metadata entry count {meta_count}")
        meta_entries = _read_exact(fh, metadata_offset + 32, meta_count * 32, file_size, "metadata entries")
        fields: dict[str, bytes] = {}
        for i in range(meta_count):
            guid = meta_entries[i * 32 : i * 32 + 16]
            item_offset, item_length = struct.unpack_from("<II", meta_entries, i * 32 + 16)
            if item_length == 0 or item_length > 1 * MB:
                raise VhdxError(f"implausible metadata item length {item_length}")
            blob = _read_exact(fh, metadata_offset + item_offset, item_length, file_size, "metadata item")
            for name, kg in (
                ("file_parameters", GUID_FILE_PARAMETERS),
                ("virtual_disk_size", GUID_VIRTUAL_DISK_SIZE),
                ("logical_sector", GUID_LOGICAL_SECTOR),
                ("physical_sector", GUID_PHYSICAL_SECTOR),
            ):
                if guid == kg:
                    fields[name] = blob

        if "file_parameters" not in fields:
            raise VhdxError("File Parameters metadata entry missing")
        block_size, flags = struct.unpack_from("<II", fields["file_parameters"], 0)
        leave_blocks_allocated = bool(flags & 0b1)
        has_parent = bool(flags & 0b10)
        if has_parent:
            raise VhdxError("differencing VHDX (HasParent) is not supported")
        if not leave_blocks_allocated:
            raise VhdxError("dynamic VHDX (LeaveBlocksAllocated=0) is not supported")
        if block_size is None or block_size & (block_size - 1) != 0 or not (1 * MB <= block_size <= 256 * MB):
            raise VhdxError(f"invalid block size {block_size} (must be a power of two in [1MiB, 256MiB])")
        virtual_disk_size = struct.unpack_from("<Q", fields["virtual_disk_size"], 0)[0] if "virtual_disk_size" in fields else 0
        logical_sector = struct.unpack_from("<I", fields["logical_sector"], 0)[0] if "logical_sector" in fields else 0
        physical_sector = struct.unpack_from("<I", fields["physical_sector"], 0)[0] if "physical_sector" in fields else 0

        # --- BAT entry 0 -> payload block 0 file offset ([MS-VHDX] 2.5.1) ---
        #   bits 0-2   : State (3 bits); PAYLOAD_BLOCK_FULLY_PRESENT == 6
        #   bits 3-19  : Reserved, MUST be 0
        #   bits 20-63 : FileOffsetMB (44 bits), offset in units of 1 MiB
        bat0_raw = _read_exact(fh, bat_offset, 8, file_size, "BAT entry 0")
        bat0 = struct.unpack("<Q", bat0_raw)[0]
        state = bat0 & 0x7
        if (bat0 >> 3) & 0x1FFFF:
            raise VhdxError("BAT entry 0 has non-zero reserved bits")
        if state != PAYLOAD_BLOCK_FULLY_PRESENT:
            raise VhdxError(f"BAT entry 0 state {state} is not PAYLOAD_BLOCK_FULLY_PRESENT (6)")
        payload_offset = (bat0 >> 20) * MB
        if payload_offset < REGION_TABLE_OFFSET + 64 * 1024:
            raise VhdxError(f"implausible payload offset {payload_offset}")
        if payload_offset + HEADER_SIZE > file_size:
            raise VhdxError("payload offset beyond end of file")

    return VhdxInfo(
        block_size=block_size,
        leave_blocks_allocated=leave_blocks_allocated,
        has_parent=has_parent,
        virtual_disk_size=virtual_disk_size,
        logical_sector_size=logical_sector,
        physical_sector_size=physical_sector,
        bat_offset=bat_offset,
        bat_entry0=bat0,
        payload_offset=payload_offset,
        file_size=file_size,
    )


def read_record_from_vhdx(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the container, read one bounded record, and return ``(payload, observed)``.

    ``observed`` includes the container fields used and the exact byte range read,
    so the caller can record precisely what was accessed.
    """
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
        if total > info.block_size:
            raise VhdxError(f"record ({total} bytes) does not fit in one {info.block_size}-byte block")
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
