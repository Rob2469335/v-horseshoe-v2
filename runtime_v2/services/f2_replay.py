"""F2 Replay Isolation — Phase 2 + Phase 3 delivery integration.

Provides the mechanism for a fresh child process to replay an already-frozen
F2 artifact without performing live retrieval.

Design invariants (from docs/LEARNING_EXPERIMENT_STATE.md §10.2):

    B. Cross-process replay via serialized manifest, not ContextVar.
    C. One authoritative delivery abstraction.

Architecture:

    Parent process
        ↓  freeze artifact → persist_manifest() → file on disk
        ↓  pass file path via env vars
    Child process
        ↓  receive path reference (SWARM_F2_REPLAY / SWARM_F2_MANIFEST_PATH)
        ↓  install_verified_replay_from_env()  ← called once at startup
        ↓  load + verify manifest
        ↓  install immutable process-level state  ← module variable, NOT ContextVar
        ↓  clear bootstrap env vars
    Delivery path (every request)
        ↓  get_delivery_artifact()  ← reads process-level state
        ↓  FROZEN → return frozen artifact text
        ↓  LIVE   → return "" (caller handles live retrieval)
    C0 is distinguished from LIVE by the ReplayState.mode field,
    NOT by empty-string check.

ContextVar is retained ONLY for test isolation — production delivery
reads the module-level _f2_state which is visible across all asyncio Tasks.
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
# In-process replay state
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


# Process-level state: visible across ALL asyncio Tasks in the process.
# This is the authoritative source for production delivery, not the ContextVar.
_f2_state: ReplayState | None = None

# Process-local "F2 replay required" flag. Distinct from replay-ACTIVE:
# an execution can be F2-required even while replay state is not yet (or no
# longer) installed. This survives the clearing of SWARM_F2_REPLAY by
# install_verified_replay_from_env(), so a lost replay before delivery still
# ABORTS rather than silently becoming LIVE.
_f2_required: bool = False

# ContextVar retained ONLY for test isolation (tests run in same process).
_REPLAY_STATE: ContextVar[ReplayState | None] = ContextVar(
    "f2_replay_state", default=None
)
_REQUIRED_CTX: ContextVar[bool] = ContextVar("f2_replay_required", default=False)


def mark_replay_required() -> None:
    """Record that the current process is an F2 replay-required execution.

    Process-local and immutable for the arm lifetime. Survives the clearing of
    SWARM_F2_REPLAY after a successful install, so the delivery seams can still
    distinguish F2_REQUIRED from ordinary non-F2 LIVE execution.
    """
    global _f2_required
    _f2_required = True
    _REQUIRED_CTX.set(True)
    _log.info("F2 replay REQUIRED — process-local flag set (survives env clear)")


def is_replay_required() -> bool:
    """True iff the current process is an F2 replay-required execution.

    Uses the process-level flag (authoritative for production delivery),
    independent of whether verified replay is currently ACTIVE.
    """
    return _f2_required


def install_replay_state(artifact: FrozenArtifact, manifest_path: str) -> ReplayState:
    """Install verified replay state in the current process.

    Sets BOTH the process-level variable (production path) and the
    ContextVar (test isolation path).  The process-level variable is
    authoritative for production delivery because ContextVars do NOT
    propagate across separately-created asyncio Tasks (FastAPI request
    handlers).
    """
    global _f2_state
    state = ReplayState(
        mode=ReplayMode.FROZEN_REPLAY,
        artifact=artifact,
        manifest_path=manifest_path,
    )
    _f2_state = state
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
    Also clears the process-local F2-required flag so a completed arm cannot
    silently re-enter LIVE while still marked F2-required.
    """
    global _f2_state, _f2_required
    _f2_state = None
    _f2_required = False
    _REPLAY_STATE.set(None)
    _REQUIRED_CTX.set(False)
    _log.info("F2 replay cleared — reverting to LIVE mode")


def get_replay_state() -> ReplayState | None:
    """Return the current replay state, or None if in LIVE mode.

    Reads from ContextVar (for test isolation).
    Production code should prefer get_delivery_artifact().
    """
    return _REPLAY_STATE.get()


def get_process_replay_state() -> ReplayState | None:
    """Return the process-level replay state.

    This is the authoritative check for production delivery.
    Module-level variable is visible across all asyncio Tasks.
    """
    return _f2_state


# ---------------------------------------------------------------------------
# One authoritative delivery abstraction
# ---------------------------------------------------------------------------

def get_delivery_artifact() -> str:
    """The one authoritative function the delivery path calls.

    Returns:
        The exact rendered artifact text if in FROZEN_REPLAY mode
        (including '' for C0).
        The empty string ONLY if not in FROZEN_REPLAY mode (LIVE mode;
        caller handles live retrieval).

    This function reads from the process-level state, which is visible
    in all asyncio Tasks (unlike ContextVar which is Task-scoped).
    """
    state = _f2_state
    if state is None or state.mode != ReplayMode.FROZEN_REPLAY:
        return ""  # LIVE mode — caller handles live retrieval
    return state.rendered_artifact


def is_replay_active() -> bool:
    """Check whether frozen replay is currently active.

    Uses process-level state (visible across all asyncio Tasks).
    """
    state = _f2_state
    return state is not None and state.mode == ReplayMode.FROZEN_REPLAY


# ---------------------------------------------------------------------------
# Cross-process reference helpers
# ---------------------------------------------------------------------------

MANIFEST_PATH_ENV = "SWARM_F2_MANIFEST_PATH"
REPLAY_REQUESTED_ENV = "SWARM_F2_REPLAY"


def encode_manifest_reference(manifest_path: Path) -> dict[str, str]:
    """Encode a manifest path and replay-request flag for child process."""
    return {
        REPLAY_REQUESTED_ENV: "1",
        MANIFEST_PATH_ENV: str(manifest_path),
    }


def decode_manifest_reference() -> Path | None:
    """Decode the manifest path from the current process environment."""
    raw = os.environ.get(MANIFEST_PATH_ENV, "")
    if not raw:
        return None
    return Path(raw)


def _clear_replay_env() -> None:
    """Clear replay env vars after successful installation."""
    os.environ.pop(REPLAY_REQUESTED_ENV, None)
    os.environ.pop(MANIFEST_PATH_ENV, None)


# ---------------------------------------------------------------------------
# Verified installation (the safe entry point)
# ---------------------------------------------------------------------------

def install_verified_replay(manifest_path: Path) -> ReplayState:
    """Load, verify, and install replay state from a persisted manifest.

    Raises FreezeVerificationError on any verification failure.
    No fallback to live retrieval on any failure.
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

    # Record F2-required BEFORE install: the env var is cleared on success, but
    # the process-local required flag must survive so a lost replay before
    # delivery still ABORTS rather than silently becoming LIVE.
    mark_replay_required()

    manifest_path = decode_manifest_reference()
    if manifest_path is None:
        raise FreezeVerificationError(
            f"Replay requested ({REPLAY_REQUESTED_ENV}=1) but "
            f"manifest reference missing ({MANIFEST_PATH_ENV} not set)"
        )

    state = install_verified_replay(manifest_path)

    _clear_replay_env()

    return state


def record_f2_delivery_evidence(
    delivered_block: str,
    sys_prompt: str,
    agent_id: str,
) -> dict:
    """Produce the F0 delivery-identity evidence at the P2 delivery seam.

    Called once per delivery inside the serving P2 process, after the final
    ``sys_prompt`` has been constructed and before messages/model invocation.
    Returns a dict with exactly 9 authoritative fields (F0 §4/§7/§9).

    Side-effect free with respect to the delivered prompt: the block and prompt
    are read-only inputs; the dict is returned to the caller for association
    with the trajectory record.
    """
    import hashlib
    import os
    import time

    delivered_sha = hashlib.sha256(delivered_block.encode("utf-8")).hexdigest()
    prompt_sha = hashlib.sha256(sys_prompt.encode("utf-8")).hexdigest()
    serving_pid = os.getpid()

    try:
        import psutil

        serving_start_time = psutil.Process(serving_pid).create_time()
    except Exception:  # noqa: BLE001
        serving_start_time = 0.0

    artifact = _f2_state.artifact if _f2_state else None

    return {
        "delivered_block": delivered_block,
        "lesson_block_hash": delivered_sha,
        "final_prompt_hash": prompt_sha,
        "delivery_timestamp": time.time(),
        "serving_pid": serving_pid,
        "serving_start_time": serving_start_time,
        "arm": agent_id,
        "manifest_treatment_set_hash": artifact.treatment_set_hash if artifact else "",
        "manifest_content_address": artifact.content_address if artifact else "",
    }
