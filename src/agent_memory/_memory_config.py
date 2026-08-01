"""Memory configuration dataclasses for the hybrid memory system."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .working_memory import WorkingMemoryEntry
from .episodic_store import Episode
from .vector_store import SearchResult


@dataclass
class MemoryConfig:
    vector_dimension: int = 768
    vector_similarity_threshold: float = 0.75
    vector_cache_size: int = 10000
    vector_similarity_metric: str = "cosine"

    episodic_max_episodes: int = 100000
    episodic_persist_path: Optional[str] = None
    episodic_auto_persist: bool = True
    episodic_persist_interval: int = 100

    working_max_size: int = 1000
    working_ttl_seconds: float = 3600.0
    working_persist_path: Optional[str] = None
    working_enable_persistence: bool = True

    semantic_recall_top_k: int = 10
    episodic_recall_limit: int = 50
    working_window_size: int = 20
    auto_embed_episodes: bool = True
    embed_fn: Optional[callable] = None


@dataclass
class MemoryContext:
    working_entries: List[WorkingMemoryEntry] = field(default_factory=list)
    conversation_window: List[Dict[str, str]] = field(default_factory=list)
    episodes: List[Episode] = field(default_factory=list)
    agent_timeline: List[Episode] = field(default_factory=list)
    semantic_results: List[SearchResult] = field(default_factory=list)

    query: str = ""
    agent_id: str = ""
    timestamp: float = field(default_factory=time.time)
    total_latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "working_entries": [
                {"key": e.key, "value": e.value, "metadata": e.metadata,
                 "importance": e.importance, "tags": list(e.tags)}
                for e in self.working_entries
            ],
            "conversation_window": self.conversation_window,
            "episodes": [ep.to_dict() for ep in self.episodes],
            "agent_timeline": [ep.to_dict() for ep in self.agent_timeline],
            "semantic_results": [
                {"text": r.entry.text, "score": r.score, "metadata": r.metadata}
                for r in self.semantic_results
            ],
            "query": self.query, "agent_id": self.agent_id,
            "timestamp": self.timestamp, "total_latency_ms": self.total_latency_ms,
        }

    def get_combined_context(self, max_chars: int = 8000) -> str:
        parts = []
        char_count = 0

        if self.working_entries:
            parts.append("### Active Context (Working Memory)")
            for entry in self.working_entries[-5:]:
                text = f"[{entry.key}] {entry.value}"
                if char_count + len(text) > max_chars: break
                parts.append(text)
                char_count += len(text)

        if self.conversation_window:
            parts.append("\n### Recent Conversation")
            for msg in self.conversation_window[-10:]:
                text = f"{msg['role']}: {msg['content']}"
                if char_count + len(text) > max_chars: break
                parts.append(text)
                char_count += len(text)

        if self.episodes:
            parts.append("\n### Relevant History (Episodic)")
            for ep in self.episodes[:10]:
                text = f"[{ep.episode_type.value}] {ep.content[:200]}"
                if char_count + len(text) > max_chars: break
                parts.append(text)
                char_count += len(text)

        if self.semantic_results:
            parts.append("\n### Related Knowledge (Semantic)")
            for result in self.semantic_results[:5]:
                text = f"[score={result.score:.2f}] {result.entry.text[:200]}"
                if char_count + len(text) > max_chars: break
                parts.append(text)
                char_count += len(text)

        return "\n".join(parts)
