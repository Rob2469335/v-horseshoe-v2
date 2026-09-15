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


def agent_workspace_root() -> Path:
    """Sandbox root for the agent tools (filesystem, sandbox_repl).

    SWARM_WORKSPACE_ROOT (absolute) when set, else the module-relative project
    root — i.e. today's behaviour. NEVER call this for repo-owned state
    (AGENTS.md, data/, config): those keep using project_root().
    """
    env_root = os.getenv("SWARM_WORKSPACE_ROOT")
    if not env_root:
        return project_root()
    p = Path(env_root)
    if not p.is_absolute():
        raise ValueError(f"SWARM_WORKSPACE_ROOT must be absolute: {env_root}")
    if not p.is_dir():
        raise ValueError(
            f"SWARM_WORKSPACE_ROOT must be an existing directory: {env_root}"
        )
    return p.resolve()


def sandbox_bounds() -> dict:
    """The effective read/write bounds the agent tools enforce, as they see them.

    Mirrors ``filesystem.py::_within_write_root`` EXACTLY: a RELATIVE
    ``SWARM_WRITE_ROOT`` resolves UNDER the workspace root (not the project
    root). Reporting the raw env vars would be misleading - the effective path
    is what the tool actually compares against.

    ``write_covers_workspace`` is the fail-closed signal. When it is False the
    agent can READ the workspace but cannot WRITE to it - the misconfiguration
    that silently voided a whole 14-task SWE batch (every patch answered
    "path is outside SWARM_WRITE_ROOT") while the run looked healthy.

    Never raises: an invalid ``SWARM_WORKSPACE_ROOT`` is reported as an error
    with ``write_covers_workspace=False`` so callers fail closed instead of
    500-ing a status endpoint.
    """
    try:
        root = agent_workspace_root()
    except ValueError as exc:
        return {
            "workspace_root": None,
            "write_root": None,
            "write_covers_workspace": False,
            "error": str(exc),
        }

    wr = os.getenv("SWARM_WRITE_ROOT")
    if not wr:
        # Unset == no extra restriction: the whole workspace is writable.
        return {
            "workspace_root": str(root),
            "write_root": None,
            "write_covers_workspace": True,
        }

    base = Path(wr)
    if not base.is_absolute():
        base = root / base
    base = base.resolve()
    try:
        root.relative_to(base)
        covers = True
    except ValueError:
        covers = False
    return {
        "workspace_root": str(root),
        "write_root": str(base),
        "write_covers_workspace": covers,
    }
