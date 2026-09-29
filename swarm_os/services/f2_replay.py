"""F2 Replay Isolation — Experiment J Phase 2.

Provides the mechanism for a fresh child process to replay an already-frozen
F2 artifact without performing live retrieval.

Design invariants (from docs/LEARNING_EXPERIMENT_STATE.md §10.2):

    B. Cross-process replay via serialized manifest, not ContextVar.
    C. One authoritative delivery abstraction.

Core principle (from F0 §3):

    Once replay mode is requested, invalid/missing/corrupt frozen evidence
    must fail closed.  It must NEVER silently fall back to live retrieval.

Architecture:

    Parent process
        ↓  freeze artifact → persist_manifest() → file on disk
        ↓  pass file path via CLI arg / env var
    Child process
        ↓  receive path reference
        ↓  load_manifest(path)
        ↓  verify_manifest(artifact)     ← Phase 1
        ↓  install_replay_state(artifact) → ContextVar (in-process)
        ↓
    Delivery path
        ↓  get_delivery_artifact()
        ↓  if replay active → return frozen artifact (NO live retrieval)
        ↓  if not active → caller handles live path (not this module's concern)
"""
from __future__ import annotations

import enum
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from swarm_os.services.f2_freeze import (
    FreezeVerificationError,
    FrozenArtifact,
    load_manifest,
    verify_manifest,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------------

class ReplayMode(enum.Enum):
    """Delivery mode for the authoritative lesson delivery abstraction."""

    LIVE = "live"              # live retrieval allowed (default / F1 path)
    FROZEN_REPLAY = "frozen"   # only verified frozen artifact; no live retrieval


# ---------------------------------------------------------------------------
# In-process replay state (ContextVar — inside child process only)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReplayState:
    """Verified replay state installed in a child process.

    Created after successful manifest load + verification.
    Immutable after installation.
    """

    mode: ReplayMode
    artifact: FrozenArtifact | None
    manifest_path: str  # path the artifact was loaded from (provenance)

    @property
    def rendered_artifact(self) -> str:
        """The exact text to deliver to the worker, or '' for C0."""
        if self.artifact is None:
            return ""
        return self.artifact.rendered_artifact


_REPLAY_STATE: ContextVar[ReplayState | None] = ContextVar(
    "f2_replay_state", default=None
)


def install_replay_state(artifact: FrozenArtifact, manifest_path: str) -> ReplayState:
    """Install verified replay state in the current process.

    Must only be called AFTER successful verification.  The caller
    is responsible for calling verify_manifest() first.

    Returns the installed ReplayState for immediate use.
    """
    state = ReplayState(
        mode=ReplayMode.FROZEN_REPLAY,
        artifact=artifact,
        manifest_path=manifest_path,
    )
    _REPLAY_STATE.set(state)
    _log.info(
        "F2 replay installed: manifest=%s arm=%s lessons=%d",
        manifest_path,
        artifact.arm,
        len(artifact.ordered_lessons),
    )
    return state


def clear_replay_state() -> None:
    """Remove replay state, reverting to LIVE mode.

    Used after an F2 arm completes to restore normal behavior.
    """
    _REPLAY_STATE.set(None)
    _log.info("F2 replay cleared — reverting to LIVE mode")


def get_replay_state() -> ReplayState | None:
    """Return the current replay state, or None if in LIVE mode."""
    return _REPLAY_STATE.get()


# ---------------------------------------------------------------------------
# One authoritative delivery abstraction
# ---------------------------------------------------------------------------

def get_delivery_artifact() -> str:
    """The one authoritative function the delivery path calls.

    Returns:
        The exact rendered artifact text if in FROZEN_REPLAY mode.
        The empty string if in LIVE mode (caller handles live retrieval).

    The delivery path in stream_runner.py should call this instead of
    directly calling render_active_lessons() when F2 replay is active.
    """
    state = _REPLAY_STATE.get()
    if state is None or state.mode != ReplayMode.FROZEN_REPLAY:
        return ""  # LIVE mode — caller handles live retrieval
    return state.rendered_artifact


def is_replay_active() -> bool:
    """Check whether frozen replay is currently active."""
    state = _REPLAY_STATE.get()
    return state is not None and state.mode == ReplayMode.FROZEN_REPLAY


# ---------------------------------------------------------------------------
# Cross-process reference helpers
# ---------------------------------------------------------------------------

# Two env vars work together:
#   SWARM_F2_REPLAY=1        → explicit "replay was requested" indicator
#   SWARM_F2_MANIFEST_PATH   → the manifest file path
#
# The parent sets BOTH before spawning; the child reads both.
# The distinction matters:
#   - Neither set             → LIVE mode (caller wants live retrieval)
#   - REPLAY=1, PATH missing  → FAIL CLOSED (replay requested but broken)
#   - REPLAY=1, PATH set      → FROZEN REPLAY (verify + install)
#   - REPLAY not set, PATH set → IGNORE the stale path (not replay mode)
MANIFEST_PATH_ENV = "SWARM_F2_MANIFEST_PATH"
REPLAY_REQUESTED_ENV = "SWARM_F2_REPLAY"


def encode_manifest_reference(manifest_path: Path) -> dict[str, str]:
    """Encode a manifest path and replay-request flag for child process.

    Returns a dict suitable for ``subprocess`` ``env`` parameter.
    Sets both REPLAY=1 and MANIFEST_PATH so the child has an unambiguous
    signal that replay was explicitly requested.
    """
    return {
        REPLAY_REQUESTED_ENV: "1",
        MANIFEST_PATH_ENV: str(manifest_path),
    }


def decode_manifest_reference() -> Path | None:
    """Decode the manifest path from the current process environment.

    Returns the Path if set, or None if not present.
    Does NOT clear env vars — clearing happens only after successful
    installation in ``install_verified_replay_from_env``.
    """
    raw = os.environ.get(MANIFEST_PATH_ENV, "")
    if not raw:
        return None
    return Path(raw)


def _clear_replay_env() -> None:
    """Clear replay env vars after successful installation.

    Only called after verification succeeds.
    """
    os.environ.pop(REPLAY_REQUESTED_ENV, None)
    os.environ.pop(MANIFEST_PATH_ENV, None)


# ---------------------------------------------------------------------------
# Verified installation (the safe entry point)
# ---------------------------------------------------------------------------

def install_verified_replay(manifest_path: Path) -> ReplayState:
    """Load, verify, and install replay state from a persisted manifest.

    This is the safe entry point for child processes.  It:

    1. Loads the manifest from disk (Phase 1 load_manifest)
    2. Verifies the manifest (Phase 1 verify_manifest)
    3. Installs the verified state as a ContextVar (in-process only)

    Raises FreezeVerificationError on any verification failure.
    Raises FileNotFoundError if the manifest file does not exist.
    Raises OSError / json.JSONDecodeError for I/O / format errors.

    There is no fallback to live retrieval on any failure.
    """
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"F2 freeze manifest not found: {manifest_path}"
        )

    artifact = load_manifest(manifest_path)
    verify_manifest(artifact)

    return install_replay_state(artifact, str(manifest_path))


def install_verified_replay_from_env() -> ReplayState | None:
    """Convenience: check replay-request flag, decode manifest, load+verify+install.

    Three outcomes:

    1. SWARM_F2_REPLAY not set → LIVE mode.  Returns None.
       The caller explicitly chose live retrieval.

    2. SWARM_F2_REPLAY=1 but manifest path missing or invalid → FAIL CLOSED.
       Raises FreezeVerificationError.  No LIVE fallback.

    3. SWARM_F2_REPLAY=1 and valid manifest → FROZEN REPLAY.
       Returns verified ReplayState.  Env vars cleared after success.

    If verification fails (step 3), the env vars remain set so the
    replay-request signal persists — the process must raise, not
    silently become LIVE.
    """
    replay_requested = os.environ.get(REPLAY_REQUESTED_ENV, "")
    if not replay_requested:
        return None  # Explicitly LIVE mode — no replay requested

    # Replay was requested.  Manifest reference is mandatory.
    manifest_path = decode_manifest_reference()
    if manifest_path is None:
        raise FreezeVerificationError(
            f"Replay requested ({REPLAY_REQUESTED_ENV}=1) but "
            f"manifest reference missing ({MANIFEST_PATH_ENV} not set)"
        )

    state = install_verified_replay(manifest_path)

    # Only clear env vars AFTER successful verification and installation.
    # If install_verified_replay raised, env vars stay set.
    _clear_replay_env()

    return state
