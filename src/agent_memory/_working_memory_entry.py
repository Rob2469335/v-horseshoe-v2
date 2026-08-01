"""WorkingMemoryEntry dataclass for LRU working memory."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Generic, Optional, Set, TypeVar

T = TypeVar('T')


@dataclass
class WorkingMemoryEntry(Generic[T]):
    key: str
    value: T
    metadata: Dict[str, Any] = field(default_factory=dict)
    importance: float = 1.0
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    ttl: Optional[float] = None
    tags: Set[str] = field(default_factory=set)

    def is_expired(self) -> bool:
        if self.ttl is None:
            return False
        return time.time() - self.created_at > self.ttl

    def touch(self) -> None:
        self.last_accessed = time.time()
        self.access_count += 1

    def effective_priority(self) -> float:
        age = time.time() - self.created_at
        recency = time.time() - self.last_accessed
        age_factor = 1.0 + (age / 3600.0)
        recency_factor = 1.0 + (recency / 60.0)
        return self.importance / (age_factor * recency_factor)
