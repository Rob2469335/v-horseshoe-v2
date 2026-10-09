"""Bounded S8 synthetic output record (Stage 1, Run 1).

Why this module exists
----------------------
A future disk-mediated S8 execution channel needs a host-side reader that
rejects hostile or malformed guest output *before* any of it is trusted.  This
module defines the smallest possible on-disk record and a strict decoder.  It is
the format the :mod:`qwen_train.f2_s8_vhdx` reader extracts.

Scope discipline
----------------
Synthetic only.  A record produced here is **not** S8 evidence; it carries no
task, no gold material, and nothing that could enter a production evidence,
admission, or readiness path.  Nothing is signed; the SHA-256 digest establishes
**record integrity only** -- never the safety of the guest, its filesystem, or
the VHDX container around it.

Format (little-endian, fixed header, exact length, no trailing bytes)
--------------------------------------------------------------------
    offset  size  field
    0       8     magic  b"F2S8REC1"
    8       2     version (uint16)  == 1
    10      2     header_size (uint16) == 48
    12      4     payload_len (uint32)  <= MAX_PAYLOAD (1 MiB)
    16      32    sha256(payload)
    48      n     payload (UTF-8 JSON object)

The decoder validates every bound before allocating, rejects a payload whose
declared length exceeds the maximum, and never interprets the payload as code.
"""
from __future__ import annotations

import hashlib
import json
import struct
from typing import Any, Mapping

__all__ = [
    "MAGIC",
    "VERSION",
    "HEADER_SIZE",
    "MAX_PAYLOAD",
    "RecordError",
    "encode_record",
    "decode_record",
    "RECORD_TOTAL_MAX",
]

MAGIC = b"F2S8REC1"
VERSION = 1
HEADER_SIZE = 48
MAX_PAYLOAD = 1 << 20  # 1 MiB
#: Magic + a maximal payload; used by the VHDX reader to bound its read.
RECORD_TOTAL_MAX = HEADER_SIZE + MAX_PAYLOAD


class RecordError(ValueError):
    """The record is malformed, truncated, oversized, or fails its digest."""


def encode_record(payload: Mapping[str, Any]) -> bytes:
    """Serialise a JSON-object payload to the canonical record bytes.

    ``sort_keys`` + compact separators make the encoding deterministic, so the
    same payload always yields the same digest.
    """
    if not isinstance(payload, Mapping):
        raise RecordError("payload must be a JSON object")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_PAYLOAD:
        raise RecordError(f"payload {len(body)} exceeds MAX_PAYLOAD {MAX_PAYLOAD}")
    digest = hashlib.sha256(body).digest()
    header = struct.pack("<8sHHI", MAGIC, VERSION, HEADER_SIZE, len(body)) + digest
    assert len(header) == HEADER_SIZE
    return header + body


def decode_record(data: bytes, *, require_exact: bool = True) -> tuple[dict[str, Any], dict[str, int]]:
    """Decode a record.  Returns ``(payload, observed)``.

    ``observed`` reports ``header_size``/``payload_len``/``total`` so a caller can
    record exactly what it parsed.

    Raises :class:`RecordError` for every rejection path: bad magic, unsupported
    version, bad header size, oversized/truncated header or payload, digest
    mismatch, trailing bytes (when ``require_exact``), invalid UTF-8, and payload
    that is not a JSON object.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise RecordError("record must be bytes")
    if len(data) < HEADER_SIZE:
        raise RecordError(f"truncated header: {len(data)} < {HEADER_SIZE}")
    magic, version, header_size, payload_len = struct.unpack("<8sHHI", data[:16])
    if magic != MAGIC:
        raise RecordError("invalid magic")
    if version != VERSION:
        raise RecordError(f"unsupported version {version}")
    if header_size != HEADER_SIZE:
        raise RecordError(f"unexpected header_size {header_size}")
    # Bounds are validated BEFORE any allocation derived from payload_len.
    if payload_len > MAX_PAYLOAD:
        raise RecordError(f"declared payload_len {payload_len} exceeds MAX_PAYLOAD {MAX_PAYLOAD}")
    total = HEADER_SIZE + payload_len
    if len(data) < total:
        raise RecordError(f"truncated payload: have {len(data)}, need {total}")
    if require_exact and len(data) != total:
        raise RecordError(f"trailing bytes: have {len(data)}, expected {total}")
    declared_digest = data[16:48]
    body = bytes(data[HEADER_SIZE:total])
    actual_digest = hashlib.sha256(body).digest()
    if declared_digest != actual_digest:
        raise RecordError("payload digest mismatch")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecordError(f"payload is not valid UTF-8: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RecordError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RecordError("payload is not a JSON object")
    observed = {"header_size": header_size, "payload_len": payload_len, "total": total}
    return payload, observed
