"""F2 rediscovery classification — LEARNING_EXPERIMENT_STATE §10.3 item 5.

Authority
---------
The rule is **not** defined here. It is F0 §6 (`docs/EXPERIMENT_J.md:101-109`),
which is frozen:

    "If a worker performs the target edit behavior at a step timestamp strictly
     earlier than the `delivery_timestamp` ... that behavior is:
       1. Recorded in the trajectory with a `rediscovery` flag
       2. Classified as pre-delivery behavior (rediscovery)
       3. Excluded from the primary T/X causal analysis
       4. Handled as missing data for that arm's primary endpoint"

    "Unambiguous: Uses strict timestamp ordering (`step.timestamp <
     delivery_timestamp`). No behavioral inference required."

§10.2 invariant H adds an evidence requirement: rediscovery is "classified from
recorded timestamps/trace evidence", recorded "as a classification rule, not
presented as proof of causality", and must "record enough evidence to determine
whether the relevant filesystem action occurred before or after lesson
delivery."

This module therefore implements ONLY the timestamp comparison plus the evidence
record. It makes no behavioural inference and asserts no causality.

Recorded-precision caveat (evidence, not a new rule)
----------------------------------------------------
Trajectory step timestamps are written with second resolution
(`agent_service_v2.py:495`, `time.strftime("%Y-%m-%dT%H:%M:%SZ", ...)`) while
`delivery_timestamp` is a float (`f2_replay.py:342`). F0 §6 compares those two
values as specified. Because a step timestamp is TRUNCATED to whole seconds, a
step can only ever appear EARLIER than it really was. That biases classification
toward `rediscovery=True`, i.e. toward EXCLUDING a run from the causal analysis —
the conservative direction for a contamination diagnostic.

The opposite bias (a genuine pre-delivery edit read as post-delivery) is not
produced by step truncation. To keep that auditable rather than hidden, every
verdict records the resolution window and sets `boundary_ambiguous` whenever the
edit and the delivery fall inside one recorded step-period of each other.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

# F0 §6 verbatim: the classification predicate.
REDISCOVERY_RULE = "step.timestamp < delivery_timestamp"

# Resolution of a recorded trajectory step timestamp (seconds).
STEP_TIMESTAMP_RESOLUTION_S = 1.0

# filesystem mutations that count as "the target edit behavior".
# Observed tool_executor.py:561 (patch/edit/update/modify) and :597
# (write/write_file/create/create_file); `filesystem.` prefixed action names are
# also matched.
_MUTATION_OPS = frozenset(
    {
        "patch", "edit", "update", "modify", "modify_file",
        "write", "write_file", "create", "create_file",
    }
)
_MUTATING_TOOL_PREFIX = "filesystem"

# Known READ-ONLY filesystem actions (approval_registry.py: the filesystem ALLOW
# tier is read/list/grep/glob). These are excluded BEFORE the permissive
# fallback so a read is never mistaken for an edit.
_READ_ONLY_OPS = frozenset(
    {"read", "list", "ls", "glob", "grep", "view", "cat", "stat", "info",
     "exists", "search", "head", "tail", "tree"}
)


class RediscoveryError(ValueError):
    """Raised when a verdict cannot be produced. Never guesses."""


def parse_step_timestamp(value: Any) -> float:
    """Parse a recorded ATIF step timestamp into epoch seconds.

    Accepts the recorded ISO-8601 ``...Z`` form, a numeric epoch, or a numeric
    string. Raises ``RediscoveryError`` on anything else so a malformed trace
    fails loudly instead of being silently treated as "not rediscovery".
    """
    if isinstance(value, bool):  # bool is an int subclass; not a timestamp
        raise RediscoveryError(f"step timestamp is a bool: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise RediscoveryError("step timestamp is empty")
        try:
            return float(text)
        except ValueError:
            pass
        iso = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(iso)
        except ValueError as exc:
            raise RediscoveryError(
                f"unparseable step timestamp {value!r}: {exc}"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    raise RediscoveryError(f"unsupported step timestamp type: {type(value).__name__}")


def _call_is_edit(call: dict[str, Any]) -> bool:
    """True when one trajectory tool call is a filesystem mutation."""
    name = str(call.get("function_name") or "").strip().lower()
    if not name:
        return False
    if name in _MUTATION_OPS:
        return True
    if name.startswith(_MUTATING_TOOL_PREFIX):
        tail = name.split(".", 1)[1] if "." in name else ""
        if not tail:
            args = call.get("arguments")
            if isinstance(args, dict):
                tail = str(args.get("action") or "").strip().lower()
        if tail in _READ_ONLY_OPS:
            return False  # a read is never an edit
        if tail in _MUTATION_OPS:
            return True
        # An UNKNOWN filesystem sub-action is treated as a mutation: that can only
        # add a candidate step, biasing toward rediscovery=True (run excluded
        # from causal analysis) — the conservative direction. Missing a real edit
        # would be the unsafe direction.
        return True
    return False


def step_is_edit(step: dict[str, Any]) -> bool:
    """True when an ATIF step record contains a filesystem mutation."""
    if not isinstance(step, dict):
        return False
    if step.get("record_type") not in (None, "step"):
        return False
    for call in step.get("tool_calls") or []:
        if isinstance(call, dict) and _call_is_edit(call):
            return True
    return False


def classify_rediscovery(
    steps: Iterable[dict[str, Any]],
    delivery_timestamp: float,
) -> dict[str, Any]:
    """Classify a run's earliest target edit against F0 §6.

    Returns the verdict plus the evidence §10.2 invariant H requires. F0 §6 item
    3 excludes a rediscovery run from the primary T/X causal analysis, so the
    caller must treat ``rediscovery=True`` as "missing data for that arm's primary
    endpoint", contributing only to contamination diagnostics.

    Raises ``RediscoveryError`` when no usable delivery_timestamp is supplied.
    A run with no edit step is NOT rediscovery (nothing to classify) and is
    reported explicitly as ``edit_observed=False``.
    """
    if isinstance(delivery_timestamp, bool) or not isinstance(
        delivery_timestamp, (int, float)
    ):
        raise RediscoveryError(
            f"delivery_timestamp must be numeric, got {delivery_timestamp!r}"
        )
    delivery = float(delivery_timestamp)

    earliest: dict[str, Any] | None = None
    candidates = 0
    for step in steps or []:
        if not step_is_edit(step):
            continue
        candidates += 1
        step_ts = parse_step_timestamp(step.get("timestamp"))
        if earliest is None or step_ts < earliest["_ts"]:
            earliest = {"_ts": step_ts, "step_id": step.get("step_id"),
                        "run_id": step.get("run_id")}

    if earliest is None:
        return {
            "rediscovery": False,
            "edit_observed": False,
            "edit_candidates": candidates,
            "rule": REDISCOVERY_RULE,
            "delivery_timestamp": delivery,
            "step_timestamp": None,
            "step_id": None,
            "step_run_id": None,
            "delta_seconds": None,
            "step_timestamp_resolution_s": STEP_TIMESTAMP_RESOLUTION_S,
            "boundary_ambiguous": False,
            "classification": "no_edit_observed",
            "excluded_from_causal_analysis": False,
            "evidence_source": "trajectory_step_records",
        }

    step_ts = earliest["_ts"]
    delta = step_ts - delivery
    rediscovery = step_ts < delivery
    ambiguous = abs(delta) < STEP_TIMESTAMP_RESOLUTION_S

    return {
        "rediscovery": rediscovery,
        "edit_observed": True,
        "edit_candidates": candidates,
        "rule": REDISCOVERY_RULE,
        "delivery_timestamp": delivery,
        "step_timestamp": step_ts,
        "step_id": earliest["step_id"],
        "step_run_id": earliest["run_id"],
        "delta_seconds": delta,
        "step_timestamp_resolution_s": STEP_TIMESTAMP_RESOLUTION_S,
        "boundary_ambiguous": ambiguous,
        "classification": "pre_delivery_rediscovery" if rediscovery
        else "post_delivery_first_edit",
        "excluded_from_causal_analysis": rediscovery,
        "evidence_source": "trajectory_step_records",
    }