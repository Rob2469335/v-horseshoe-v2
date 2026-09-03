"""Stable OpenCode Go session header.

OpenCode Go requires an ``x-opencode-session`` header on every request (one
stable ID per conversation) so it can optimize the service; requests missing it
may error after 2026-09-06. This module provides that header for both the
litellm path (``extra_headers=opencode_headers()``) and direct httpx/OpenAI
clients (merge ``opencode_headers()`` into the request headers).

The ID is stable for the life of the process (or taken from
``OPENCODE_SESSION_ID`` when set). Zero internal imports so it can be imported
from both runtime_v2 and swarm_os without circular-import risk.
"""

from __future__ import annotations

import os
import uuid

_SESSION_ID: str | None = None


def opencode_session_id() -> str:
    """Return a stable session ID (env override, else per-process UUID)."""
    global _SESSION_ID
    env = os.getenv("OPENCODE_SESSION_ID", "").strip()
    if env:
        return env
    if _SESSION_ID is None:
        _SESSION_ID = uuid.uuid4().hex
    return _SESSION_ID


def opencode_headers() -> dict:
    """Headers to attach to every OpenCode Go request."""
    return {"x-opencode-session": opencode_session_id()}


def is_opencode_base(base: str | None) -> bool:
    """True when an api_base points at OpenCode (zen free or zen/go paid)."""
    return bool(base) and "opencode.ai" in str(base)
