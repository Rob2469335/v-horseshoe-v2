"""Durable JSONL store helpers for the chess trainer.

Retention rule (research-grounded, 2026): a JSONL archive is NEVER compacted
lossily and NEVER silently truncated — a save that keeps only the newest N
entries is documented data loss ("compaction is a lossy operation"; see also
The Cascade Log, arXiv 2606.05467: a hot-500 window over a ~14.5K history
addresses <2% of the evidence). Every save is therefore ATOMIC (tmp +
os.replace so a crash mid-write cannot corrupt the committed archive) and
records an auditable manifest (total + SHA-256 of the committed bytes) next
to the store, so retention is visible and tamper-evident.

Eviction is a VIEW decision (list/due limits in the API layer), never a
write-path trim. Fail-closed: store-write failures raise to the caller (which
logs per its existing pattern); a manifest-write failure is logged here and
does NOT masquerade as a lost archive (the store itself was already committed).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MANIFEST_POLICY = "archive-all; eviction is a view decision, never a write-path trim"


def manifest_path(store_path: Path) -> Path:
    """The manifest file that accompanies a given store file."""
    return store_path.with_suffix(store_path.suffix + ".manifest.json")


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Atomically write `rows` to `path` (tmp file + os.replace in the same
    directory). If the process dies mid-write the tmp file is orphaned but the
    previous committed archive is untouched. Raises on IO failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            fh.flush()
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_manifest(store_path: Path, rows: list[dict[str, Any]]) -> None:
    """Record retention state in <store_path>.manifest.json: total + SHA-256 of
    the store's committed bytes (hashed from what is truly on disk). Written
    atomically. Raises on IO failure."""
    if store_path.exists():
        data = store_path.read_bytes()
    else:
        data = b"".join(json.dumps(r).encode("utf-8") + b"\n" for r in rows)
    manifest = {
        "store": store_path.name,
        "total": len(rows),
        "sha256": hashlib.sha256(data).hexdigest(),
        "policy": _MANIFEST_POLICY,
        "updated_at": time.time(),
    }
    mpath = manifest_path(store_path)
    fd, tmp = tempfile.mkstemp(
        dir=str(mpath.parent), prefix=mpath.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest, indent=2) + "\n")
            fh.flush()
        os.replace(tmp, mpath)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Atomic full-archive write + manifest. The store commit is authoritative;
    a manifest failure is logged and swallowed (the archive IS safe)."""
    atomic_write_jsonl(path, rows)
    try:
        write_manifest(path, rows)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "chess store manifest write failed (archive IS committed) for %s: %s",
            path,
            exc,
        )
