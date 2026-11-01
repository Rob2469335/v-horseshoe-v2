"""Immutable verified snapshot of a probe's task inputs and patches.

WHY
---
The signed grant binds the selection, the pool and the registration chain, but
not the per-task ``<task>.json`` record or the ``.test_patch`` / ``.gold_patch``
files the builder consumes. Hashing those files at the moment of use would
still leave a time-of-check/time-of-use gap: the build reopens the path it read.

This module closes that gap for one run: every file is read once, hashed, and
written byte-for-byte into a run-private snapshot; the builder is then pointed
at the snapshot, so the bytes it consumes are the bytes that were verified.

WHAT IT DOES NOT DO
-------------------
It cannot prove the owner authorized those bytes: the grant schema carries no
inputs-manifest digest. Binding the manifest digest into the expected-grant spec
and the signed record is a schema extension and an owner decision.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import re
import secrets
import time
from typing import Iterable, Sequence

_TASK_RE = re.compile(r"^[A-Za-z0-9_.\-]+__[A-Za-z0-9_.\-]+-\d+$")

#: (suffix, sub-directory) of every file a build consumes for one task.
_REQUIRED_FILES = ((".json", "inputs"), (".test_patch", "patches"), (".gold_patch", "patches"))


class InputIntegrityError(RuntimeError):
    """The inputs/patches cannot be verified as a complete, unambiguous set."""


@dataclasses.dataclass(frozen=True)
class VerifiedInputs:
    root: pathlib.Path
    inputs_dir: pathlib.Path
    patches_dir: pathlib.Path
    task_ids: tuple[str, ...]
    manifest: tuple[dict[str, object], ...]
    manifest_digest: str


def _canonical(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_task_ids(task_ids: Sequence[str]) -> tuple[str, ...]:
    ids = tuple(task_ids)
    if not ids:
        raise InputIntegrityError("no selected task ids were supplied")
    for task_id in ids:
        if not isinstance(task_id, str) or not _TASK_RE.fullmatch(task_id):
            raise InputIntegrityError(
                f"task id is not a safe task directory name: {task_id!r}"
            )
    if len(set(ids)) != len(ids):
        raise InputIntegrityError("duplicate task id in the selected set")
    return ids


def _reject_unexpected_siblings(base: pathlib.Path, task_id: str, expected: set[str]) -> None:
    """Refuse stray files that claim a selected task's namespace."""
    for path in base.iterdir():
        if not path.is_file() or not path.name.startswith(task_id + "."):
            continue
        if path.name not in expected:
            raise InputIntegrityError(
                f"unexpected file for selected task {task_id!r}: {path.name!r}"
            )


def snapshot_verified_inputs(
    inputs: pathlib.Path | str,
    patches: pathlib.Path | str,
    *,
    task_ids: Iterable[str],
    dest_root: pathlib.Path | str,
) -> VerifiedInputs:
    """Read, hash, and copy the exact bytes for ``task_ids`` into a snapshot."""
    ids = _validate_task_ids(tuple(task_ids))
    inputs_base = pathlib.Path(inputs)
    patches_base = pathlib.Path(patches)
    for base, label in ((inputs_base, "inputs"), (patches_base, "patches")):
        if not base.is_dir():
            raise InputIntegrityError(f"{label} directory does not exist: {base}")

    dest = pathlib.Path(dest_root) / (
        f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}-"
        f"{secrets.token_hex(4)}"
    )
    (dest / "inputs").mkdir(parents=True)
    (dest / "patches").mkdir(parents=True)

    manifest: list[dict[str, object]] = []
    for task_id in ids:
        expected = {task_id + suffix for suffix, _ in _REQUIRED_FILES}
        _reject_unexpected_siblings(inputs_base, task_id, expected)
        _reject_unexpected_siblings(patches_base, task_id, expected)
        for suffix, kind in _REQUIRED_FILES:
            src = (inputs_base if kind == "inputs" else patches_base) / (task_id + suffix)
            if not src.is_file():
                raise InputIntegrityError(
                    f"missing {kind} file for {task_id!r}: {src.name}"
                )
            data = src.read_bytes()  # read exactly once
            digest = hashlib.sha256(data).hexdigest()
            dst = dest / kind / (task_id + suffix)
            with open(dst, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if hashlib.sha256(dst.read_bytes()).hexdigest() != digest:
                raise InputIntegrityError(
                    f"snapshot write did not preserve bytes for {src.name}"
                )
            manifest.append(
                {
                    "task_id": task_id,
                    "kind": kind,
                    "name": src.name,
                    "bytes": len(data),
                    "sha256": digest,
                }
            )

    manifest.sort(key=lambda row: (str(row["task_id"]), str(row["kind"]), str(row["name"])))
    manifest_digest = hashlib.sha256(_canonical(manifest)).hexdigest()
    (dest / "manifest.json").write_bytes(
        _canonical({"schema": "f2-s8-input-manifest/v1",
                    "manifest_digest": manifest_digest,
                    "files": manifest})
    )
    return VerifiedInputs(
        root=dest,
        inputs_dir=dest / "inputs",
        patches_dir=dest / "patches",
        task_ids=ids,
        manifest=tuple(manifest),
        manifest_digest=manifest_digest,
    )


def verify_snapshot_unchanged(verified: VerifiedInputs) -> None:
    """Fail if the snapshot the build consumes no longer matches its manifest.

    This checks the **snapshot** directories (``verified.inputs_dir`` /
    ``patches_dir``), not the original sources. It detects a changed, replaced,
    missing or unexpected file and a manifest digest that no longer describes
    the observed set. It cannot prevent a task from modifying its own snapshot
    while that task is building.
    """
    observed: list[dict[str, object]] = []
    for kind, base in (
        ("inputs", verified.inputs_dir),
        ("patches", verified.patches_dir),
    ):
        expected = {
            str(row["name"]): row
            for row in verified.manifest
            if row["kind"] == kind
        }
        if not base.is_dir():
            raise InputIntegrityError(f"snapshot {kind} directory missing: {base}")
        for path in sorted(base.iterdir()):
            if not path.is_file():
                raise InputIntegrityError(
                    f"unexpected non-file entry in snapshot {kind}: {path.name}"
                )
            row = expected.pop(path.name, None)
            if row is None:
                raise InputIntegrityError(
                    f"unexpected file in snapshot {kind}: {path.name}"
                )
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != row["sha256"]:
                raise InputIntegrityError(
                    f"snapshot file changed or replaced: {kind}/{path.name}"
                )
            observed.append(
                {
                    "task_id": row["task_id"],
                    "kind": kind,
                    "name": row["name"],
                    "bytes": len(data),
                    "sha256": digest,
                }
            )
        if expected:
            missing = sorted(expected)[0]
            raise InputIntegrityError(f"snapshot file missing: {kind}/{missing}")

    observed.sort(
        key=lambda row: (str(row["task_id"]), str(row["kind"]), str(row["name"]))
    )
    if hashlib.sha256(_canonical(observed)).hexdigest() != verified.manifest_digest:
        raise InputIntegrityError("snapshot manifest digest does not describe the observed set")


def verify_sources_unchanged(
    verified: VerifiedInputs,
    inputs: pathlib.Path | str,
    patches: pathlib.Path | str,
) -> None:
    """Fail if the source files drifted after the snapshot was taken.

    The snapshot is what the build consumes, so drift here is evidence of a
    concurrent writer, not a reason to rebuild from the source.
    """
    inputs_base = pathlib.Path(inputs)
    patches_base = pathlib.Path(patches)
    for row in verified.manifest:
        base = inputs_base if row["kind"] == "inputs" else patches_base
        src = base / str(row["name"])
        if not src.is_file():
            raise InputIntegrityError(f"source disappeared after snapshot: {src}")
        if hashlib.sha256(src.read_bytes()).hexdigest() != row["sha256"]:
            raise InputIntegrityError(f"source changed after snapshot: {src}")
