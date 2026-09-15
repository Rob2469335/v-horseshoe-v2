"""Atomic, serialized writers for AGENTS.md.

AGENTS.md is written concurrently by FOUR independent paths — the CLI watchman
thread (``watch_loop._audit_write``), the backend healing daemon
(``recovery_engine._record_to_agents_md``), the reflection daemon
(``reflection_loop._record_rule_to_agents_md``), and the ``skill_manage`` tool
(``tool_executor``). Each previously did a full-file read-modify-write with
``Path.write_text``, which is NOT atomic: it opens the file with mode ``w``
(truncating it to 0 bytes) and only then writes. An interruption between the two
— a kill, an exception, or a concurrent writer that was not holding the same
lock — leaves AGENTS.md truncated. This happened for real: the file was found at
0 bytes mid-session after the runtime writers clobbered it (recovered from git).

Two guarantees here, applied to every writer:

1. **Serialization** — one ``filelock`` per target file, so the four writers
   cannot interleave a read-modify-write (the previous code lock-guarded only two
   of the four, so an unlocked writer could race a locked one).
2. **Atomicity** — content is staged to a sibling ``*.tmp.<uuid>`` and promoted
   with ``os.replace``, so the real file is only ever whole-old or whole-new and
   is never observed at 0 bytes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from filelock import FileLock

from swarm_os.lib.atomic_io import atomic_write_text
from swarm_os.lib.paths import project_root

_LOCK_TIMEOUT = 10.0


def agents_md_path() -> Path:
    """Absolute path to the project's AGENTS.md (independent of the process cwd)."""
    return project_root() / "AGENTS.md"


def insert_after_marker(content: str, marker: str, entry: str) -> str | None:
    """Return ``content`` with ``entry`` inserted right after ``marker``.

    Returns ``None`` (no change) when the marker is absent, preserving the
    original writers' "marker in content" guard.
    """
    if marker not in content:
        return None
    return content.replace(marker, marker + "\n" + entry, 1)


def update_agents_md(
    transform: Callable[[str], str | None],
    path: Path | None = None,
) -> bool:
    """Serialized read-modify-write of an AGENTS.md file.

    ``transform`` receives the current content and returns the new content, or
    ``None`` to leave the file untouched. Returns True when a write happened. The
    whole cycle holds the file's lock and ends in an atomic replace, so concurrent
    callers never lose an update and a failure never truncates the file.
    """
    target = Path(path) if path is not None else agents_md_path()
    if not target.exists():
        return False
    with FileLock(str(target) + ".lock", timeout=_LOCK_TIMEOUT):
        content = target.read_text(encoding="utf-8")
        new_content = transform(content)
        if not new_content or new_content == content:
            return False
        atomic_write_text(target, new_content)
        return True
