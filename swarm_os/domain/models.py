# swarm_os/domain/models.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

VALID_ROLES   = {"worker", "coordinator", "observer", "disabled"}
VALID_STATUSES = {"pending", "running", "completed", "failed", "cancelled"}


@dataclass
class SwarmNode:
    node_id: str               = field(default_factory=lambda: str(uuid4()))
    name:    str               = "node"
    role:    str               = "worker"
    state:   dict[str, Any]   = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            raise ValueError(f"Invalid role {self.role!r}. Must be one of {VALID_ROLES}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwarmNode":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SwarmJob:
    job_id:  str               = field(default_factory=lambda: str(uuid4()))
    kind:    str               = "default"
    payload: dict[str, Any]   = field(default_factory=dict)
    status:  str               = "pending"

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid status {self.status!r}. Must be one of {VALID_STATUSES}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwarmJob":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
