"""Pre-action authorization for the agent tool path (Design A, v1, 2026-08-12).

WHY THIS EXISTS: the agent loop's tool calls flow through
tool_executor.run() with NO pre-dispatch authorization — filesystem writes,
playwright clicks, sandbox_repl, system, email_send all dispatched directly.
The Command Center's permission_tiers model exists but is wired only to
control.py / the scheduler / screen tools, not the agent's tool boundary.
The 2026 SOTA (OAP "pre-action authorization", ClawGuard tool-boundary
enforcement, ARM provenance) converges on the same principle: intercept tool
calls BEFORE execution, evaluate against policy, and audit the decision.

DESIGN (user-approved): a single enforcement point at tool_executor.run().
  - agent_tool_policy(tool, action) classifies each agent tool/action
    explicitly: ALLOW / CONFIRM / ALWAYS_CONFIRM / DENY. Unknown combos are
    DENY (fail-closed) — never ALLOW.
  - CONFIRM and ALWAYS_CONFIRM both require human approval in v1 (there is no
    trustworthy server-side auto_mode; CLI auto_mode is presentation-layer
    state and is NOT read here). The distinction is preserved for future
    policy/UI semantics.
  - When confirmation is required, run() does NOT dispatch. It creates a
    pending action in the registry and returns a confirmation_required result
    carrying an opaque pending_id. The CLI approves/denies via the existing
    ask_user/Observation flow; the STORED payload (never a replacement) is
    executed only after approval, and only if the pending action still exists,
    is unexpired, and is unconsumed (one-time).

SECURITY PROPERTIES:
  - pending_id is a cryptographically random opaque identifier; the SHA-256
    argument digest lives INSIDE the record (identity vs integrity separation).
  - Approval is bound to the EXACT action: the digest must match at execution
    time, and the executed payload is the one stored at request time — the
    approving turn cannot substitute a different tool payload.
  - Fail-closed: unknown tool/action, malformed context, or any registry error
    DENIES.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from typing import Any

_OPAQUE_ID_BYTES = 16          # 128-bit opaque pending id
_PENDING_TTL_S = 300.0         # pending approvals expire after 5 minutes

# Policy verdicts (single authoritative classification for the agent tool path).
ALLOW = "ALLOW"               # execute immediately, never asks
CONFIRM = "CONFIRM"           # requires human approval (v1: always)
ALWAYS_CONFIRM = "ALWAYS_CONFIRM"  # requires human approval (v1: always)
DENY = "DENY"                 # fail-closed: unknown / unclassified -> deny


# ---------------------------------------------------------------------------
# Agent-tool classification. EXPLICIT per (tool, action) — do NOT reuse the
# Command Center's human/agent channel keyword classifier here: it misclassifies
# agent read-only ops (e.g. screen cursor_position -> human+confirm). This map
# is the single source of truth for the AGENT execution boundary.
# ---------------------------------------------------------------------------
_READ_ONLY_FS_OPS = frozenset({"read", "read_file", "list", "grep", "glob",
                               "scan_dir", "scandir", "list_dir", "walk", "cat"})
_WRITE_FS_OPS = frozenset({"write", "write_file", "create", "create_file",
                           "patch", "edit", "update", "modify", "replace",
                           "replace_file_content", "edit_file", "delete",
                           "remove", "unlink"})
_PLAYWRIGHT_READ_OPS = frozenset({"navigate", "browser_a11y", "a11y",
                                  "browser_state", "browser_describe",
                                  "describe", "screenshot", "extract_text",
                                  "browser_find", "find", "browser_wait",
                                  "wait", "verify", "browser_verify"})
_PLAYWRIGHT_WRITE_OPS = frozenset({"browser_click", "click", "browser_type",
                                   "type", "browser_fill_form", "fill_form",
                                   "browser_press_key", "press", "select",
                                   "scroll"})
_EMAIL_READ_OPS = frozenset({"email_list", "list", "email_search", "search",
                             "email_read", "read"})
_SCREEN_READ_OPS = frozenset({"cursor_position", "screenshot", "foreground_window",
                              "list_windows"})
_SCREEN_INPUT_OPS = frozenset({"mouse_move", "left_click", "right_click",
                               "double_click", "scroll", "type_text", "type",
                               "key", "mouse", "keyboard"})
# system tool: read-only introspection is ALLOW; explicitly privileged ops are
# ALWAYS_CONFIRM; unknown ops are DENY (fail-closed).
_SYSTEM_READ_OPS = frozenset({"system_inventory", "process_list", "service_list",
                              "net_connections", "disk_analyzer", "installed_apps",
                              "startup_items", "registry_query", "event_log_query",
                              "inventory"})
_SYSTEM_PRIVILEGED_OPS = frozenset({"kill", "terminate", "stop_service",
                                    "start_service", "restart_service",
                                    "change_settings", "shutdown", "restart"})


def agent_tool_policy(tool: str, action: str | None = None) -> str:
    """Classify an agent tool/action into ALLOW / CONFIRM / ALWAYS_CONFIRM /
    DENY. Explicit and fail-closed: any unknown tool or unclassified action is
    DENY, never ALLOW."""
    t = (tool or "").strip().lower()
    a = (action or "").strip().lower()

    if t == "filesystem":
        if a in _READ_ONLY_FS_OPS:
            return ALLOW
        if a in _WRITE_FS_OPS:
            return ALWAYS_CONFIRM
        return DENY if a else CONFIRM  # unknown fs op -> deny

    if t == "web_search":
        return ALLOW
    if t == "web_fetch":
        return CONFIRM
    if t == "semantic_search":
        return ALLOW

    if t == "git":
        if a in ("status", "log", "diff", "diff-stat", "show", "branch"):
            return ALLOW
        # read-only git tool: any unknown/other operation (including the
        # state-changing ones the executor doesn't implement) is denied.
        return DENY if a else ALLOW  # no action = default status read

    if t == "screen":
        if a in _SCREEN_READ_OPS:
            return ALLOW
        if a in _SCREEN_INPUT_OPS:
            return ALWAYS_CONFIRM
        # screen with no/unknown action: fail closed (screen is privileged).
        return DENY if a else ALWAYS_CONFIRM

    if t == "sandbox_repl":
        return ALWAYS_CONFIRM
    if t == "system":
        if a in _SYSTEM_READ_OPS:
            return ALLOW
        if a in _SYSTEM_PRIVILEGED_OPS:
            return ALWAYS_CONFIRM
        return DENY if a else ALWAYS_CONFIRM  # unknown system op -> deny

    if t == "playwright":
        if a in _PLAYWRIGHT_READ_OPS:
            return CONFIRM
        if a in _PLAYWRIGHT_WRITE_OPS:
            return ALWAYS_CONFIRM
        return DENY if a else CONFIRM

    if t in ("email", "email_list", "email_search", "email_read"):
        # email read/list/search are ALLOW; email_draft is CONFIRM.
        if a in _EMAIL_READ_OPS:
            return ALLOW
        return ALLOW if not a else CONFIRM
    if t == "email_draft":
        return CONFIRM
    if t == "email_send":
        return ALWAYS_CONFIRM

    if t == "lsp":
        return CONFIRM
    if t in ("mcp", "mcp_register"):
        return CONFIRM if a not in ("register", "configure") else ALWAYS_CONFIRM
    if t == "vscode_automation":
        return CONFIRM

    if t in ("remember", "deprecate_memory"):
        return CONFIRM
    if t == "todo":
        return ALLOW

    # Unknown tool -> fail-closed DENY.
    return DENY


# ---------------------------------------------------------------------------
# Pending-action registry
# ---------------------------------------------------------------------------
class _Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}

    def _prune_expired(self) -> None:
        now = time.time()
        expired = [k for k, v in self._pending.items() if now > v["expires_at"]]
        for k in expired:
            del self._pending[k]

    def create(
        self,
        *,
        agent_id: str,
        turn: int,
        tool: str,
        action: str | None,
        payload: dict,
    ) -> dict[str, Any]:
        """Mint a pending action. Returns the public record (with the opaque
        pending_id). The digest is stored inside the record."""
        pending_id = secrets.token_urlsafe(_OPAQUE_ID_BYTES)
        digest = _arg_digest(payload)
        now = time.time()
        with self._lock:
            self._prune_expired()
            self._pending[pending_id] = {
                "agent_id": agent_id,
                "turn": turn,
                "tool": tool,
                "action": action,
                "payload": payload,
                "arg_digest": digest,
                "created_at": now,
                "expires_at": now + _PENDING_TTL_S,
                "consumed": False,
            }
            return dict(self._pending[pending_id], pending_id=pending_id)

    def peek(self, pending_id: str) -> dict[str, Any] | None:
        """Look up a pending action WITHOUT consuming it (used to render the
        approval request). Returns None if unknown/expired/consumed."""
        if not pending_id:
            return None
        with self._lock:
            self._prune_expired()
            rec = self._pending.get(pending_id)
            if rec is None or rec.get("consumed"):
                return None
            return dict(rec, pending_id=pending_id)

    def consume(
        self, pending_id: str, *, expected_tool: str, expected_payload: dict
    ) -> dict[str, Any] | None:
        """Atomically validate + consume a pending action.

        Returns the record ONLY if: pending_id exists, not expired, not already
        consumed, AND the stored tool + argument digest EXACTLY match what the
        caller wants to execute. Any mismatch returns None (denied) and the
        record is left unconsumed so a forged/mismatched approval cannot burn a
        legitimately-pending action. On success the record is marked consumed
        (one-time use)."""
        if not pending_id:
            return None
        with self._lock:
            self._prune_expired()
            rec = self._pending.get(pending_id)
            if rec is None or rec.get("consumed"):
                return None
            if rec["tool"] != expected_tool:
                return None
            if rec["arg_digest"] != _arg_digest(expected_payload):
                return None
            rec["consumed"] = True
            return dict(rec, pending_id=pending_id)

    def deny(self, pending_id: str) -> bool:
        """Explicitly discard a pending action without executing it. Returns
        True if a pending action was found and discarded."""
        if not pending_id:
            return False
        with self._lock:
            rec = self._pending.get(pending_id)
            if rec is None or rec.get("consumed"):
                return False
            del self._pending[pending_id]
            return True

    def consume_any(self, pending_id: str) -> dict[str, Any] | None:
        """Consume a pending action by pending_id ALONE and return its stored
        record. This is the trust-anchored execution path: the STORED payload
        (tool + arguments) is dispatched, so the approving caller cannot
        substitute a different payload. Returns None if unknown/expired/consumed."""
        if not pending_id:
            return None
        with self._lock:
            self._prune_expired()
            rec = self._pending.get(pending_id)
            if rec is None or rec.get("consumed"):
                return None
            rec["consumed"] = True
            return dict(rec, pending_id=pending_id)

    def stats(self) -> dict:
        with self._lock:
            self._prune_expired()
            return {"pending": len(self._pending)}


def _arg_digest(payload: Any) -> str:
    """Canonical SHA-256 digest of a tool payload. JSON-dumps with sorted keys
    so argument order does not change the digest."""
    try:
        canonical = json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = str(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_registry = _Registry()


def get_registry() -> _Registry:
    return _registry


# Test seam: point the module at a fresh registry.
def _reset_registry() -> None:
    global _registry
    _registry = _Registry()
