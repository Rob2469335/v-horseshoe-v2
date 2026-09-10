"""Shared, deterministic project-root resolution.

The backend is normally launched from the project root (start-dev.ps1 does
``Set-Location $root``), so ``os.getcwd()`` happens to equal the project root in
the supported path. But tests and embeddings chdir, which silently relocates any
``os.getcwd()``-based path (logs, screenshots, the event-bus patch log, an LSP
workspace root). Resolve the root deterministically from this module's own path
instead, honoring ``ZENITH_PROJECT_ROOT`` as an explicit override — the same
convention ``tool_executor._ROOT`` and ``playwright`` already use.
"""
from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT_CACHE: Path | None = None


def project_root() -> Path:
    """Return the project root as a Path.

    Resolution order:
      1. ``ZENITH_PROJECT_ROOT`` env var (explicit override).
      2. The directory containing ``AGENTS.md`` starting from this module's
         location (deterministic — does not depend on the process cwd).
      3. ``os.getcwd()`` as a last-resort fallback.

    Result is cached, so repeated calls are cheap and stable regardless of any
    later ``os.chdir`` in the process.
    """
    global _PROJECT_ROOT_CACHE
    if _PROJECT_ROOT_CACHE is not None:
        return _PROJECT_ROOT_CACHE

    override = os.getenv("ZENITH_PROJECT_ROOT")
    candidate: Path | None = Path(override).expanduser() if override else None
    if candidate is not None:
        resolved = candidate.resolve()
    else:
        # this file: swarm_os/lib/paths.py -> parents[2] = project root
        resolved = Path(__file__).resolve().parents[2]

    if not (resolved / "AGENTS.md").exists():
        resolved = Path.cwd()
    _PROJECT_ROOT_CACHE = resolved
    return resolved