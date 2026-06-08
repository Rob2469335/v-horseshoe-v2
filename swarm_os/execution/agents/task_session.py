"""
Module: task_session
Order: 22
Package: execution.agents
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class TaskSession:
    session_id: str = field(default_factory=lambda: str(uuid4()))
    task_name: str = ""
    state: str = "created"
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "task_name": self.task_name,
            "state": self.state,
            "created_at": self.created_at.isoformat(),
        }