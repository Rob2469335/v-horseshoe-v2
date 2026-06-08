"""
Module: event_types
Order: 7
Package: foundation.events
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    SESSION_STARTED = "session.started"
    SESSION_COMPLETED = "session.completed"
    TOOL_CALLED = "tool.called"
    TOOL_FAILED = "tool.failed"
    OUTCOME_RECORDED = "outcome.recorded"
    EXPERIMENT_STARTED = "experiment.started"
    EXPERIMENT_COMPLETED = "experiment.completed"
    POLICY_CHANGED = "policy.changed"
    PROMOTION_APPLIED = "promotion.applied"
    ROLLBACK_APPLIED = "rollback.applied"