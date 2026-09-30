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
        ↓  load_manifest(path)
        ↓  verify_manifest(artifact)     ← Phase 1
        ↓  install immutable process-level state  ← module variable, NOT ContextVar
        ↓  clear bootstrap env vars
    Delivery path (every request)
        ↓  get_delivery_artifact()  ← reads process-level state
        ↓  FROZEN → return frozen artifact text
        ↓  LIVE   → return "" (caller handles live retrieval)
        ↓
    C0 is distinguished from LIVE by the ReplayState.mode field,
    NOT by empty-string check.

ContextVar is retained ONLY for test isolation — production delivery
reads the module-level _f2_state which is visible across all asyncio Tasks.
"""
from runtime_v2.services.f2_replay import (
    MANIFEST_PATH_ENV,
    REPLAY_REQUESTED_ENV,
    ReplayMode,
    ReplayState,
    clear_replay_state,
    decode_manifest_reference,
    encode_manifest_reference,
    get_delivery_artifact,
    get_process_replay_state,
    get_replay_state,
    install_replay_state,
    install_verified_replay,
    install_verified_replay_from_env,
    is_replay_active,
    is_replay_required,
    mark_replay_required,
)

__all__ = [
    "MANIFEST_PATH_ENV",
    "REPLAY_REQUESTED_ENV",
    "ReplayMode",
    "ReplayState",
    "clear_replay_state",
    "decode_manifest_reference",
    "encode_manifest_reference",
    "get_delivery_artifact",
    "get_process_replay_state",
    "get_replay_state",
    "install_replay_state",
    "install_verified_replay",
    "install_verified_replay_from_env",
    "is_replay_active",
    "is_replay_required",
    "mark_replay_required",
]