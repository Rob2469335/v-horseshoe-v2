"""S8 disk-mediated channel: the host<->guest request/result protocol.

Two directions, one envelope
----------------------------
The binary envelope (:mod:`qwen_train.f2_s8_record`, ``F2S8REC1``) and the strict
payload schema + staged reader (:mod:`qwen_train.f2_s8_channel`) already exist and
are tested.  This module adds the *protocol* built on them:

    host: stage_request  ->  <channel>/requests/<job_id>.rec
    guest: read_request   (consume + validate the staged plan)
    guest: write_result   ->  <channel>/results/<job_id>.rec
    host: read_result     (consume + validate + replay-check the retained result)

Transport integrity only
------------------------
:func:`read_result` returns a :class:`qwen_train.f2_s8_channel.ChannelVerdict`
whose ``ok`` is **transport validity, nothing more**.  It refuses any payload
that claims an outcome status (``completed``/``failed``); outcome evidence
travels only through :mod:`qwen_train.f2_evidence` against retained artifacts.
A valid result record is a *lead*, never a verdict.

Honest scope
------------
This module implements the **disk protocol** — request staging, request
consumption, result publication, host-side result reading, replay detection,
bounded reads and crash recovery.  It does **not** implement the in-guest
execution itself: that is the ``execute_in_guest`` seam in
:class:`HostGuestChannel`, which is the one step that requires the authorized,
owner-provisioned guest (an attested VM and the host->guest dispatch path).  No
network, subprocess, credential or Hyper-V call appears here, so the protocol
can be exercised end-to-end against a local directory standing in for the guest
scratch disk.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from qwen_train.f2_s8_channel import (
    CHANNEL_SCHEMA,
    ChannelVerdict,
    read_channel_file,
)
from qwen_train.f2_s8_record import encode_record
from qwen_train.f2_s8_runner import publish_atomic
from qwen_train.f2_s8_session import TaskSpec

__all__ = [
    "TransportError",
    "build_request_payload",
    "build_result_payload",
    "stage_request",
    "read_request",
    "write_result",
    "read_result",
    "pending_requests",
    "HostGuestChannel",
]

#: Transportable statuses only.  ``completed``/``failed`` are outcome claims and
#: are refused by the channel schema.
REQUEST_STATUS = "planned"
RESULT_STATUS = "attempted"
EXECUTE_PHASE = "EXECUTE_S8"


class TransportError(Exception):
    """The disk protocol contract was violated."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class _Names:
    """The safe path components shared by both directions."""

    task_id: str
    job_id: str
    source: str

    def __post_init__(self) -> None:
        for label, value in (
            ("task_id", self.task_id), ("job_id", self.job_id), ("source", self.source)
        ):
            if not str(value or "").strip():
                raise TransportError(f"{label} is required")
            if "/" in value or "\\" in value or ".." in value:
                raise TransportError(f"{label} {value!r} is not a safe path component")


def build_request_payload(
    *,
    task_id: str,
    job_id: str,
    source: str,
    provenance: Mapping[str, Any],
    created_at: str = "",
    note: str = "",
) -> dict[str, Any]:
    """The transportable ``plan`` payload staged for the guest to consume."""
    if not provenance:
        raise TransportError("provenance is required and may not be empty")
    payload: dict[str, Any] = {
        "schema_version": CHANNEL_SCHEMA,
        "payload_kind": "plan",
        "task_id": str(task_id),
        "job_id": str(job_id),
        "source": str(source),
        "phase": EXECUTE_PHASE,
        "status": REQUEST_STATUS,
        "created_at": created_at or _utc_now(),
        "provenance": dict(provenance),
    }
    if note:
        payload["note"] = str(note)
    return payload


def build_result_payload(
    *,
    task_id: str,
    job_id: str,
    source: str,
    provenance: Mapping[str, Any],
    artifact_name: str,
    artifact_sha256: str,
    artifact_size: int,
    created_at: str = "",
    note: str = "",
) -> dict[str, Any]:
    """The transportable ``evidence`` payload the guest publishes.

    Carries only the retained artifact's *identity* (name + digest + size), never
    a verdict.  The status is lifecycle-only (``attempted``).
    """
    if not provenance:
        raise TransportError("provenance is required and may not be empty")
    payload: dict[str, Any] = {
        "schema_version": CHANNEL_SCHEMA,
        "payload_kind": "evidence",
        "task_id": str(task_id),
        "job_id": str(job_id),
        "source": str(source),
        "phase": EXECUTE_PHASE,
        "status": RESULT_STATUS,
        "created_at": created_at or _utc_now(),
        "provenance": dict(provenance),
        "artifact": {
            "name": str(artifact_name),
            "sha256": str(artifact_sha256),
            "size_bytes": int(artifact_size),
        },
        "execution": {"state": "not_established"},
    }
    if note:
        payload["note"] = str(note)
    return payload


def stage_request(
    *,
    channel_dir: Path | str,
    task_id: str,
    job_id: str,
    source: str,
    provenance: Mapping[str, Any],
    created_at: str = "",
    note: str = "",
) -> Path:
    """Wrap a plan payload in the envelope and publish it atomically."""
    names = _Names(task_id=task_id, job_id=job_id, source=source)
    payload = build_request_payload(
        task_id=names.task_id, job_id=names.job_id, source=names.source,
        provenance=provenance, created_at=created_at, note=note,
    )
    target = Path(channel_dir) / "requests" / f"{names.job_id}.rec"
    publish_atomic(target, encode_record(payload))
    return target


def write_result(
    *,
    channel_dir: Path | str,
    task_id: str,
    job_id: str,
    source: str,
    provenance: Mapping[str, Any],
    artifact_name: str,
    artifact_sha256: str,
    artifact_size: int,
    created_at: str = "",
    note: str = "",
) -> Path:
    """Wrap a result payload in the envelope and publish it atomically (guest side)."""
    names = _Names(task_id=task_id, job_id=job_id, source=source)
    payload = build_result_payload(
        task_id=names.task_id, job_id=names.job_id, source=names.source,
        provenance=provenance, artifact_name=artifact_name,
        artifact_sha256=artifact_sha256, artifact_size=artifact_size,
        created_at=created_at, note=note,
    )
    target = Path(channel_dir) / "results" / f"{names.job_id}.rec"
    publish_atomic(target, encode_record(payload))
    return target


def read_request(
    path: Path | str,
    *,
    expected_task_id: str | None = None,
    expected_job_id: str | None = None,
    expected_source: str | None = None,
) -> ChannelVerdict:
    """Consume and validate a staged request (guest side).  Transport-only."""
    return read_channel_file(
        path,
        expected_task_id=expected_task_id,
        expected_job_id=expected_job_id,
        expected_source=expected_source,
    )


def read_result(
    path: Path | str,
    *,
    expected_task_id: str | None = None,
    expected_job_id: str | None = None,
    expected_source: str | None = None,
    seen_job_ids: Sequence[str] | set[str] | None = None,
) -> ChannelVerdict:
    """Consume and validate a published result (host side).  Transport-only.

    ``seen_job_ids`` turns a re-delivered result into a ``REPLAYED_JOB_ID``
    rejection instead of a second, indistinguishable observation.
    """
    return read_channel_file(
        path,
        expected_task_id=expected_task_id,
        expected_job_id=expected_job_id,
        expected_source=expected_source,
        seen_job_ids=seen_job_ids,
    )


def pending_requests(channel_dir: Path | str) -> tuple[Path, ...]:
    """Every staged request with no matching published result (crash recovery).

    A result is matched by the identical ``<job_id>.rec`` file name, so a process
    that died after staging but before the guest published leaves its request
    visible and is safe to re-dispatch.
    """
    requests = Path(channel_dir) / "requests"
    results = Path(channel_dir) / "results"
    if not requests.is_dir():
        return ()
    done = {p.name for p in results.glob("*.rec")} if results.is_dir() else set()
    return tuple(sorted(p for p in requests.glob("*.rec") if p.name not in done))


@dataclass
class HostGuestChannel:
    """Host-side driver for the disk protocol.

    ``execute_in_guest(request_path, result_path)`` is the **external seam**: it
    must cause the attested guest to consume the staged request and publish a
    result.  It is the one step this module deliberately does not implement.  A
    local implementation may stand in for it in tests; the protocol, replay and
    recovery logic around it are the same either way.
    """

    channel_dir: Path
    execute_in_guest: Callable[[Path, Path], None]

    def dispatch(
        self,
        *,
        spec: TaskSpec,
        job_id: str,
        provenance: Mapping[str, Any],
        seen_job_ids: Sequence[str] | set[str] | None = None,
        created_at: str = "",
    ) -> ChannelVerdict:
        """Stage a request, run the guest, then validate the published result."""
        names = _Names(
            task_id=spec.task_id,
            job_id=job_id,
            source=str(spec.payload.get("source") or ""),
        )
        request_path = stage_request(
            channel_dir=self.channel_dir,
            task_id=names.task_id,
            job_id=names.job_id,
            source=names.source,
            provenance=provenance,
            created_at=created_at,
        )
        result_path = Path(self.channel_dir) / "results" / f"{names.job_id}.rec"
        self.execute_in_guest(request_path, result_path)
        return read_result(
            result_path,
            expected_task_id=names.task_id,
            expected_job_id=names.job_id,
            expected_source=names.source,
            seen_job_ids=seen_job_ids,
        )
