"""Risk-classified permission model (2026 SOTA — two axes: TIER and CHANNEL).

Every SHIPPING desktop agent converged on the same core idea: what KIND of action
matters more than which tool. This is the single permission model that Builds 2
(screen input tiers), 3 (scheduler ceiling), and 4 (takeover channel) all key off.

TWO AXES — tier and channel answer different questions and must NOT be conflated:
  TIER    (how dangerous is this action?)   free / ask / important / approval
  CHANNEL (who is allowed to perform it?)    agent / human

  free       -> always runs, never asks (read/glob/grep/search/status)
  ask        -> asks unless the target (domain/app) is granted
  important  -> ALWAYS confirms (send/delete/purchase/submit/settings-change),
                never auto-approved even in auto mode
  approval   -> requires an explicit approval-tier grant (OS control, screen
                input, terminal)
  channel human -> the AGENT cannot perform this action at all; only a human via
                a separate input path (login/password/payment entry). This is the
                slot Build 4's threat-model research evaluates a mechanism against.

Resolution is DETERMINISTIC and FAIL-CLOSED:
  - tier_for:    explicit tool mapping wins; else action keyword class; else "ask"
                 (unknown tool -> fail-closed ask, never free).
  - channel_for: a known takeover-class action -> "human"; EVERYTHING ELSE ->
                 "human" by default (unknown tool/action must NOT silently let the
                 agent perform something that should require a human). Only
                 actions explicitly known to be agent-safe resolve to "agent".
  - needs_confirmation: important/approval ALWAYS True (even auto); free always
                 False; ask = not auto_mode.

Grants are persisted per target (domain for web, app name for OS) in
data/permission_grants.json: {"gmail.com": {"send": "important"}, ...}. The same
target field unifies web + OS, so a browser's screen-input tier resolves by the
ACTIVE TAB's domain, not the app name — one grant model, no parallel system.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

GRANTS_FILE = Path("data/permission_grants.json")

# tool -> base tier (falls back to the action-class default)
_TOOL_TIERS = {
    "read": "free",
    "glob": "free",
    "grep": "free",
    "search": "free",
    "write": "ask",
    "patch": "ask",
    "web_search": "free",
    "web_fetch": "ask",
    "email_list": "free",
    "email_search": "free",
    "email_read": "free",
    "email_thread": "free",
    "email_summarize_thread": "free",
    "email_unsubscribe_scan": "free",
    "email_digest": "free",
    "email_draft": "ask",
    "email_reply_draft": "ask",
    "email_manage": "important",
    "email_send": "important",
    "playwright": "ask",
    "filesystem": "ask",
    "sandbox_repl": "approval",
    "system": "approval",
    "screen": "approval",
    "terminal": "approval",
}

# Keyword -> risk tier (an action is classified by the FIRST matching group).
_IMPORTANT_TERMS = (
    "send",
    "delete",
    "purchase",
    "submit",
    "checkout",
    "pay",
    "settings_change",
    "approve",
    "confirm",
    "transfer",
    "refund",
)
_APPROVAL_TERMS = (
    "system",
    "screen",
    "sandbox_repl",
    "terminal",
    "powershell",
    "mouse",
    "keyboard",
    "key",
    "input",
    "click",
    "scroll",
)
_ASK_TERMS = (
    "write",
    "patch",
    "type",
    "click",
    "navigate",
    "fill",
    "select",
    "press",
    "draft",
)

# Keyword -> channel. The KNOWN human-channel cases (login/password/payment entry).
_HUMAN_TERMS = (
    "login",
    "password",
    "passwd",
    "secret",
    "payment",
    "card",
    "ccv",
    "cvv",
    "pin",
    "credential",
    "takeover",
    "otp",
    "2fa",
    "mfa",
    "bank",
    "signin",
    "sign-in",
)
# Actions explicitly known to be agent-safe -> channel "agent".
_AGENT_TERMS = (
    "read",
    "glob",
    "grep",
    "list",
    "search",
    "navigate",
    "a11y",
    "screenshot",
    "state",
    "status",
    "describe",
    "verify",
    "find",
    "fill",
    "type",
    "click",
    "press",
    "select",
    "scroll",
    "wait",
    "email_list",
    "email_search",
    "email_read",
    "email_draft",
    "email_thread",
    "email_summarize_thread",
    "email_unsubscribe_scan",
    "email_digest",
    "web_search",
    "web_fetch",
    "filesystem_read",
    "extract_text",
)

_lock = threading.Lock()
_grants_cache = None


def _load_grants() -> dict:
    global _grants_cache
    if _grants_cache is not None:
        return _grants_cache
    data = {}
    try:
        if GRANTS_FILE.exists():
            data = json.loads(GRANTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    _grants_cache = data
    return data


def _save_grants(data: dict) -> None:
    try:
        GRANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        GRANTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _action_class(action: str) -> str:
    """Map a tool/action name to a risk tier by keyword. Important > approval >
    ask > free. Unknown -> 'ask' (fail-closed)."""
    a = (action or "").lower().strip()
    if any(f in a for f in _IMPORTANT_TERMS):
        return "important"
    if any(f in a for f in _APPROVAL_TERMS):
        return "approval"
    if any(f in a for f in _ASK_TERMS):
        return "ask"
    return "ask"  # fail-closed: unknown action is not free


def base_tier(tool: str, action: str | None = None) -> str:
    """The default risk tier for (tool, action). Explicit tool mapping wins, else
    the action-class default, else 'ask' (fail-closed for unknown tools)."""
    if tool in _TOOL_TIERS:
        return _TOOL_TIERS[tool]
    if action:
        return _action_class(action)
    return "ask"


def tier_for(target: str, tool: str, action: str | None = None) -> str:
    """The RESOLVED tier for (target, tool, action), honoring per-target grants."""
    base = base_tier(tool, action)
    with _lock:
        grants = _load_grants().get(target or "")
        if grants:
            for key in (action or "", tool):
                if key and key in grants:
                    return grants[key]
    return base


def has_grant(target: str, key: str) -> bool:
    """True if `target` has an EXPLICIT grant for `key`. This lets callers
    distinguish 'a higher tier was GRANTED for this target' from 'the tool's
    base tier default' — for OS/screen input, the absence of a grant must mean
    the safe default (view-only), never the tool's approval-tier fallback."""
    with _lock:
        grants = _load_grants().get(target or "")
        return bool(grants and key in grants)


def channel_for(target: str, tool: str, action: str | None = None) -> str:
    """WHO performs this action: 'agent' or 'human'.

    FAIL-CLOSED BY DEFAULT: only actions explicitly known to be agent-safe (in
    _AGENT_TERMS, or granted 'agent') resolve to 'agent'. Everything else —
    including UNKNOWN tools/actions — resolves to 'human', so a future tool that
    touches a login/payment flow without being classified still cannot let the
    agent perform it silently. Worst case is a needless over-ask, never a silent
    agent-side credential touch."""
    a = (action or "").lower().strip()
    if a and any(f in a for f in _HUMAN_TERMS):
        return "human"
    with _lock:
        grants = _load_grants().get(target or "")
        if grants:
            for key in (action or "", tool):
                if key and key in grants:
                    # a grant can explicitly route to human (e.g. set_grant(d, 'login', 'human'))
                    if grants[key] == "human":
                        return "human"
                    break
    if a and any(f in a for f in _AGENT_TERMS):
        return "agent"
    # The tool name itself may be agent-safe even when no action is supplied
    # (e.g. channel_for('x', 'read') with no action).
    t = (tool or "").lower().strip()
    if t and any(f in t for f in _AGENT_TERMS):
        return "agent"
    return "human"  # fail-closed default: unknown -> human


def set_grant(target: str, key: str, value: str) -> bool:
    """Persist a per-target grant: set_grant('gmail.com', 'send', 'important').
    value may be a tier (free/ask/important/approval) or 'human' (channel)."""
    if value not in ("free", "ask", "important", "approval", "human"):
        return False
    with _lock:
        data = _load_grants()
        data.setdefault(target, {})[key] = value
        _save_grants(data)
        global _grants_cache
        _grants_cache = data
    return True


def needs_confirmation(
    target: str, tool: str, action: str | None = None, auto_mode: bool = False
) -> bool:
    """True if this (target, tool, action) needs a human confirmation NOW.
    important/approval ALWAYS confirm (even in auto mode); free never; ask
    confirms unless auto_mode. A human-channel action always confirms."""
    if channel_for(target, tool, action) == "human":
        return True
    tier = tier_for(target, tool, action)
    if tier in ("important", "approval"):
        return True
    if tier == "free":
        return False
    return not auto_mode  # ask: confirm unless auto


def is_scheduler_allowed(target: str, tool: str, action: str | None = None) -> bool:
    """Build 3 ceiling: may a SCHEDULED task (which fires with nobody watching)
    perform this action? important/approval tiers and human-channel actions are
    HARD-BLOCKED unconditionally — a task description can never talk its way
    around the ceiling (same as never_self_modify's directory rule)."""
    if channel_for(target, tool, action) == "human":
        return False
    tier = tier_for(target, tool, action)
    return tier in ("free", "ask")


def all_grants() -> dict:
    with _lock:
        return dict(_load_grants())
