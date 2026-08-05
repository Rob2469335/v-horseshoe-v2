"""
Module: policy_record
Order: 4
Package: foundation.memory
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class PolicyRecord:
    policy_id: str = field(default_factory=lambda: str(uuid4()))
    policy_name: str = ""
    version: str = "0.0.1"
    enabled: bool = True
    rules: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_name": self.policy_name,
            "version": self.version,
            "enabled": self.enabled,
            "rules": self.rules,
            "updated_at": self.updated_at.isoformat(),
        }
