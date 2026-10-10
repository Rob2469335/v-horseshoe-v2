"""Disk-mediated channel: bounded payload schema + staged host-side reader.

Transport vs science
--------------------
The binary **envelope** already exists and is tested: :mod:`qwen_train.f2_s8_record`
(fixed 48-byte little-endian header, ``F2S8REC1``, protocol version, uint32
payload length, SHA-256 over the exact payload bytes, 1 MiB hard cap, no
trailing bytes) and :mod:`qwen_train.f2_s8_vhdx`, the unattached FIXED-VHDX
reader.  This module does **not** duplicate either.  It adds the two things they
deliberately do not do:

1. a strict **payload schema** (``f2_s8_channel_v1``) with identity, status and
   replay validation, and
2. a **staged host reader** that separates read -> parse -> verify -> decode ->
   schema-validate -> identity/replay-check -> persist-or-reject.

Reader safety contract
----------------------
The reader consumes one explicitly named file.  It never mounts a filesystem,
never launches a VM, never invokes guest code, never runs a script, and never
uses a payload value as a command, import path, module name, or shell argument.
A guest-supplied ``artifact.name`` is validated as a **bare file name** and is
returned as data only — the reader does not open it.

Trust boundary (read this before believing a record)
----------------------------------------------------
SHA-256 in the envelope detects accidental or unauthorised modification of the
payload **only when the expected digest, or a trust relationship binding it, is
independent of the writer**.  A digest computed by the same untrusted guest and
carried inside the guest's own bytes authenticates nothing: it proves the bytes
are self-consistent, not that the guest is honest or that the guest's claims
are true.  Envelope integrity is therefore **transport integrity**, never
scientific validity — that remains :mod:`qwen_train.f2_evidence`'s verdict.

Evidence label: **Reader mechanics; hostile guest-written disk not tested live.**
"""
from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from qwen_train.f2_s8_job import (
    ALLOWED_TRANSITIONS,
    JOB_STATUSES,
    PHASES,
)
from qwen_train.f2_s8_record import (
    HEADER_SIZE,
    MAGIC,
    MAX_PAYLOAD,
    RecordError,
    decode_record,
)

__all__ = [
    "CHANNEL_SCHEMA",
    "REQUIRED_PAYLOAD_FIELDS",
    "OPTIONAL_PAYLOAD_FIELDS",
    "ChannelError",
    "ChannelVerdict",
    "read_bounded_bytes",
    "parse_envelope",
    "decode_payload",
    "validate_payload",
    "read_channel_file",
]

CHANNEL_SCHEMA = "f2_s8_channel_v1"

REQUIRED_PAYLOAD_FIELDS = (
    "schema_version",
    "payload_kind",
    "task_id",
    "job_id",
    "source",
    "phase",
    "status",
    "created_at",
    "provenance",
)

OPTIONAL_PAYLOAD_FIELDS = ("artifact", "execution", "note")

#: Payload kinds.  ``plan`` records intent; ``evidence`` claims retained
#: execution output.  Either way this reader only establishes transport-level
#: validity.
PAYLOAD_KINDS = ("plan", "evidence")

#: Hard ceiling on the whole file: envelope header plus a maximal payload.
MAX_FILE_BYTES = HEADER_SIZE + MAX_PAYLOAD

_HEX = set("0123456789abcdef")

#: A bare file name: no separators, no traversal, no Windows-reserved or
#: shell-significant characters, no control characters.
_SAFE_NAME_BAD = set('<>:"|?*;&') | {"/", "\\"}


class ChannelError(ValueError):
    """A channel stage rejected the input."""


def _code_for_record_error(message: str) -> str:
    m = message
    if m.startswith("invalid magic"):
        return "BAD_MAGIC"
    if m.startswith("unsupported version"):
        return "UNSUPPORTED_VERSION"
    if m.startswith("truncated header"):
        return "TRUNCATED_HEADER"
    if m.startswith("unexpected header_size"):
        return "INVALID_LENGTH"
    if "exceeds MAX_PAYLOAD" in m:
        return "OVERSIZED_PAYLOAD"
    if m.startswith("truncated payload"):
        return "TRUNCATED_PAYLOAD"
    if "digest mismatch" in m:
        return "DIGEST_MISMATCH"
    if m.startswith("trailing bytes"):
        return "TRAILING_BYTES"
    if "not valid UTF-8" in m:
        return "INVALID_UTF8"
    if "not valid JSON" in m:
        return "MALFORMED_JSON"
    if "not a JSON object" in m:
        return "MALFORMED_JSON"
    if "must be bytes" in m:
        return "MALFORMED_FIELD"
    return "MALFORMED_FIELD"


@dataclass(frozen=True)
class ChannelVerdict:
    """Outcome of one read.  ``ok`` is transport validity, nothing more."""

    ok: bool
    stage: str
    reason_codes: list[str] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    observed: dict[str, int] = field(default_factory=dict)
    detail: str = ""

    @property
    def is_execution_evidence(self) -> bool:
        """Always ``False``: a valid envelope never establishes S8 evidence."""
        return False

    def __bool__(self) -> bool:
        return self.ok


def _reject(stage: str, *codes: str, detail: str = "") -> ChannelVerdict:
    return ChannelVerdict(
        ok=False, stage=stage, reason_codes=list(codes), detail=detail
    )


# --------------------------------------------------------------------------- #
# Stage 1 — read bounded bytes
# --------------------------------------------------------------------------- #
def read_bounded_bytes(path: Path | str, *, max_total: int = MAX_FILE_BYTES) -> bytes:
    """Read at most ``max_total`` bytes from an explicitly named file.

    Refuses a file whose size already exceeds the envelope's own ceiling, so an
    oversized claim can never cause a large allocation.  Never follows a
    guest-supplied name: the caller passes the path.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise ChannelError(f"cannot stat {p}: {exc}") from exc
    if size > max_total:
        raise ChannelError(
            f"file size {size} exceeds the hard cap {max_total}; refusing to read"
        )
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise ChannelError(f"cannot read {p}: {exc}") from exc
    if len(data) > max_total:
        raise ChannelError("file grew past the hard cap while reading")
    return data


# --------------------------------------------------------------------------- #
# Stage 2 — parse the envelope
# --------------------------------------------------------------------------- #
def parse_envelope(data: bytes) -> tuple[dict[str, Any], dict[str, int]]:
    """Validate magic/version/length/digest/truncation and decode UTF-8 + JSON.

    A zero-length payload is reported as ``EMPTY_PAYLOAD`` rather than as
    generic malformed JSON, but only when the header is otherwise well formed —
    so bad magic, bad version and bad header size still win the race to be
    reported first.
    """
    if len(data) >= HEADER_SIZE and data[:8] == MAGIC:
        version, header_size, payload_len = struct.unpack_from("<HHI", data, 8)
        if version == 1 and header_size == HEADER_SIZE and payload_len == 0:
            raise ChannelError("EMPTY_PAYLOAD: declared payload length is zero")
    try:
        payload, observed = decode_record(data)
    except RecordError as exc:
        raise ChannelError(f"{_code_for_record_error(str(exc))}: {exc}") from exc
    return payload, observed


# --------------------------------------------------------------------------- #
# Stage 3 — strict payload schema
# --------------------------------------------------------------------------- #
def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _validate_artifact_name(name: Any) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if len(name) > 255:
        return False
    if name in (".", "..") or ".." in name:
        return False
    if any(ch in _SAFE_NAME_BAD for ch in name):
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return False
    if name != name.strip():
        return False
    return True


def decode_payload(raw: Mapping[str, Any] | bytes) -> dict[str, Any]:
    """Decode a JSON payload object, rejecting empty or non-object content."""
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) == 0:
            raise ChannelError("EMPTY_PAYLOAD: payload is zero bytes")
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ChannelError(f"INVALID_UTF8: {exc}") from exc
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ChannelError(f"MALFORMED_JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ChannelError("MALFORMED_JSON: payload is not a JSON object")
        return obj
    if not isinstance(raw, dict):
        raise ChannelError("MALFORMED_JSON: payload is not a JSON object")
    return dict(raw)


def validate_payload(
    payload: Mapping[str, Any],
    *,
    expected_task_id: str | None = None,
    expected_job_id: str | None = None,
    expected_source: str | None = None,
    seen_job_ids: Sequence[str] | set[str] | None = None,
    previous_status: str | None = None,
) -> ChannelVerdict:
    """Schema + identity + replay validation of a decoded payload."""
    obj = dict(payload)
    if not obj:
        return _reject("schema", "EMPTY_PAYLOAD")

    missing = [k for k in REQUIRED_PAYLOAD_FIELDS if k not in obj]
    if missing:
        return _reject("schema", "MISSING_FIELD", detail=f"missing {missing}")

    allowed = set(REQUIRED_PAYLOAD_FIELDS) | set(OPTIONAL_PAYLOAD_FIELDS)
    unexpected = sorted(set(obj) - allowed)
    if unexpected:
        # Strict by design: an unknown key may be an attempt to smuggle a
        # command, a path, or a future-meaning field past this reader.
        return _reject("schema", "UNEXPECTED_FIELD", detail=f"unexpected {unexpected}")

    if obj["schema_version"] != CHANNEL_SCHEMA:
        return _reject("schema", "WRONG_SCHEMA_VERSION")
    if obj["payload_kind"] not in PAYLOAD_KINDS:
        return _reject("schema", "UNEXPECTED_FIELD", detail="bad payload_kind")
    if obj["phase"] not in PHASES:
        return _reject("schema", "UNEXPECTED_FIELD", detail="bad phase")
    if obj["status"] not in JOB_STATUSES:
        return _reject("schema", "INVALID_STATUS")
    for key in ("task_id", "job_id", "source", "created_at"):
        if not isinstance(obj[key], str) or not obj[key].strip():
            return _reject("schema", "MALFORMED_FIELD")
    if not isinstance(obj["provenance"], dict) or not obj["provenance"]:
        return _reject("schema", "PROVENANCE_ABSENT")

    artifact = obj.get("artifact")
    if artifact is not None:
        if not isinstance(artifact, dict):
            return _reject("schema", "MALFORMED_FIELD")
        art_keys = set(artifact)
        if art_keys - {"name", "sha256", "size_bytes"}:
            return _reject("schema", "UNEXPECTED_FIELD", detail="artifact keys")
        if not _validate_artifact_name(artifact.get("name")):
            return _reject("identity", "UNTRUSTED_FIELD_VALUE", detail="artifact.name")
        if not _is_sha256(artifact.get("sha256")):
            return _reject("schema", "MALFORMED_FIELD", detail="artifact.sha256")
        size = artifact.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return _reject("schema", "MALFORMED_FIELD", detail="artifact.size_bytes")

    execution = obj.get("execution")
    if execution not in (None, {}, {"state": "not_established"}):
        return _reject("schema", "PLAN_PRESENTED_AS_EXECUTION")

    if expected_task_id is not None and obj["task_id"] != expected_task_id:
        return _reject("identity", "WRONG_TASK_ID")
    if expected_job_id is not None and obj["job_id"] != expected_job_id:
        return _reject("identity", "WRONG_JOB_ID")
    if expected_source is not None and obj["source"] != expected_source:
        return _reject("identity", "WRONG_SOURCE")

    if seen_job_ids is not None and obj["job_id"] in set(seen_job_ids):
        return _reject("replay", "REPLAYED_JOB_ID")

    if previous_status is not None:
        if previous_status not in JOB_STATUSES:
            return _reject("transition", "INVALID_STATUS")
        if obj["status"] not in ALLOWED_TRANSITIONS.get(previous_status, ()):
            return _reject("transition", "INVALID_STATUS_TRANSITION")

    return ChannelVerdict(ok=True, stage="validated", payload=obj)


# --------------------------------------------------------------------------- #
# Stage 4 — the staged reader
# --------------------------------------------------------------------------- #
def read_channel_file(
    path: Path | str,
    *,
    expected_task_id: str | None = None,
    expected_job_id: str | None = None,
    expected_source: str | None = None,
    seen_job_ids: Sequence[str] | set[str] | None = None,
    previous_status: str | None = None,
    sink: Callable[[Mapping[str, Any]], Any] | None = None,
) -> ChannelVerdict:
    """Read, parse, verify, decode, validate, check replay, then persist.

    ``sink`` is invoked **only** on a fully validated record, so a rejection at
    any earlier stage provably leaves the caller's trusted state untouched.
    The reader itself never opens the guest-named artifact and never executes
    any payload value.
    """
    try:
        data = read_bounded_bytes(path)
    except ChannelError as exc:
        msg = str(exc)
        code = "FILE_TOO_LARGE" if "hard cap" in msg else "IO_ERROR"
        if "cannot stat" in msg or "cannot read" in msg:
            code = "IO_ERROR"
        return _reject("read", code, detail=msg)

    try:
        payload, observed = parse_envelope(data)
    except ChannelError as exc:
        code = str(exc).split(":", 1)[0]
        return _reject("envelope", code, detail=str(exc))

    if int(observed.get("payload_len", 0)) == 0:
        # Defensive: parse_envelope already refuses a zero-length payload.
        return _reject("decode", "EMPTY_PAYLOAD", detail="declared payload length is zero")

    try:
        obj = decode_payload(payload)
    except ChannelError as exc:
        code = str(exc).split(":", 1)[0]
        return _reject("decode", code, detail=str(exc))

    verdict = validate_payload(
        obj,
        expected_task_id=expected_task_id,
        expected_job_id=expected_job_id,
        expected_source=expected_source,
        seen_job_ids=seen_job_ids,
        previous_status=previous_status,
    )
    if not verdict.ok:
        return ChannelVerdict(
            ok=False,
            stage=verdict.stage,
            reason_codes=verdict.reason_codes,
            detail=verdict.detail,
            observed=observed,
        )

    if sink is not None:
        sink(verdict.payload)
    return ChannelVerdict(
        ok=True,
        stage="validated",
        payload=verdict.payload,
        observed=observed,
        detail="transport valid; scientific validity NOT established by this reader",
    )


def envelope_digest(data: bytes) -> str:
    """SHA-256 of the raw envelope bytes (diagnostic aid, not authentication)."""
    return hashlib.sha256(data).hexdigest()
