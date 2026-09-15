"""Atomic file writes — the canonical helper for durable output.

Motivation (a real incident, twice): a plain ``Path.write_text`` / ``open(p, "w")``
truncates the target to 0 bytes BEFORE writing. If the process is interrupted
between the two — a kill, a crash, a concurrent writer — the durable file is left
empty or half-written. That already cost AGENTS.md once (found at 0 bytes) and it
threatens every long-run result file (a 2-hour harvest whose output is truncated
at the end is a total loss).

``atomic_write_text`` stages the content to a sibling ``*.tmp.<uuid>`` and
promotes it with ``os.replace`` (atomic on the same volume). The target is only
ever whole-old or whole-new, and the temp is removed on any failure — so a crash
at any point leaves the PREVIOUS valid output intact, never a corrupted one.

Use this for anything durable that is rewritten wholesale: result JSONL/JSON,
state snapshots, index/manifest files, agent-written markdown. It is NOT needed
for append-only log tails (``open(p, "a")``) — appends don't truncate.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable


def _tmp_sibling(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex}")


def _promote(tmp: Path, path: Path) -> None:
    try:
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_text(path: Path | str, content: str) -> None:
    """Write ``content`` to ``path`` atomically; ``path`` is never truncated."""
    path = Path(path)
    tmp = _tmp_sibling(path)
    try:
        tmp.write_text(content, encoding="utf-8")
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    _promote(tmp, path)


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (for non-text payloads)."""
    path = Path(path)
    tmp = _tmp_sibling(path)
    try:
        tmp.write_bytes(data)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    _promote(tmp, path)


def atomic_write_json(path: Path | str, obj: Any, *, indent: int | None = 2) -> None:
    """Serialize ``obj`` as JSON and write it atomically."""
    atomic_write_text(path, json.dumps(obj, indent=indent, ensure_ascii=False))


def atomic_write_jsonl(path: Path | str, rows: Iterable[dict]) -> None:
    """Write an iterable of dicts as JSON Lines, atomically."""
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    atomic_write_text(path, lines)
