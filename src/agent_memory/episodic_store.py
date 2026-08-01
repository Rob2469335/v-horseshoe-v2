"""
Episodic Timeline Store for Agent Memory.

Inspired by AutoGen's conversation history structures - maintains chronological
event timeline with rich metadata for temporal reasoning and replay capabilities.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from .vector_store import VectorStore, SearchResult, SimilarityMetric


class EpisodeType(Enum):
    """Types of episodic events."""
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_MESSAGE = "agent_message"
    USER_MESSAGE = "user_message"
    SYSTEM_EVENT = "system_event"
    ERROR = "error"
    DECISION = "decision"
    REASONING = "reasoning"
    REFLECTION = "reflection"
    PLANNING = "planning"
    HANDOFF = "handoff"
    CHECKPOINT = "checkpoint"


@dataclass
class Episode:
    """Single episodic memory entry."""
    id: str
    episode_type: EpisodeType
    timestamp: float
    agent_id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)
    tags: Set[str] = field(default_factory=set)
    importance: float = 1.0  # 0.0 - 1.0
    embedding: Optional[np.ndarray] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "episode_type": self.episode_type.value,
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
            "content": self.content,
            "metadata": self.metadata,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "tags": list(self.tags),
            "importance": self.importance,
            "embedding": self.embedding.tolist() if self.embedding is not None else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Episode":
        """Deserialize from dictionary."""
        episode = cls(
            id=data["id"],
            episode_type=EpisodeType(data["episode_type"]),
            timestamp=data["timestamp"],
            agent_id=data["agent_id"],
            content=data["content"],
            metadata=data.get("metadata", {}),
            parent_id=data.get("parent_id"),
            children_ids=data.get("children_ids", []),
            tags=set(data.get("tags", [])),
            importance=data.get("importance", 1.0),
        )
        if data.get("embedding"):
            episode.embedding = np.array(data["embedding"])
        return episode


@dataclass
class EpisodicQuery:
    """Query for episodic memory retrieval."""
    agent_id: Optional[str] = None
    episode_types: Optional[List[EpisodeType]] = None
    time_range: Optional[Tuple[float, float]] = None  # (start, end) timestamps
    tags: Optional[Set[str]] = None
    min_importance: float = 0.0
    limit: int = 50
    reverse_chronological: bool = True
    query_text: Optional[str] = None  # For semantic search
    similarity_threshold: float = 0.7


class EpisodicStore:
    """
    Episodic Timeline Store - AutoGen-inspired conversation history.
    
    Features:
    - Chronological event timeline with full metadata
    - Hierarchical episode structure (parent/child relationships)
    - Tag-based filtering and importance weighting
    - Semantic search via vector embeddings
    - Temporal queries (time ranges, recency)
    - Session/agent isolation
    - Persistence to disk
    """
    
    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        max_episodes: int = 100000,
        persistence_path: Optional[Path] = None,
        auto_persist: bool = True,
        persist_interval: int = 100,
    ):
        """
        Initialize episodic store.
        
        Args:
            vector_store: Optional vector store for semantic search
            max_episodes: Maximum episodes to keep in memory
            persistence_path: Path for persistent storage
            auto_persist: Auto-save after N episodes
            persist_interval: Episodes between auto-saves
        """
        self.vector_store = vector_store
        self.max_episodes = max_episodes
        self.persistence_path = persistence_path
        self.auto_persist = auto_persist
        self.persist_interval = persist_interval
        
        # Main storage: episode_id -> Episode
        self._episodes: Dict[str, Episode] = {}
        
        # Indexes for fast queries
        self._by_agent: Dict[str, List[str]] = defaultdict(list)
        self._by_type: Dict[EpisodeType, List[str]] = defaultdict(list)
        self._by_tag: Dict[str, List[str]] = defaultdict(list)
        self._chronological: List[str] = []  # episode IDs in time order
        
        # Session tracking
        self._current_session_id: Optional[str] = None
        self._session_episodes: Dict[str, List[str]] = defaultdict(list)
        
        # Stats
        self._episode_count = 0
        self._stats = {
            "total_episodes": 0,
            "total_queries": 0,
            "semantic_queries": 0,
            "temporal_queries": 0,
            "avg_query_latency_ms": 0.0,
        }
        
        self._lock = asyncio.Lock()

        # Fire-and-forget task tracking (strong refs prevent GC mid-await)
        self._bg_tasks: Set[asyncio.Task] = set()

        # Load persisted data if available
        if persistence_path and persistence_path.exists():
            task = asyncio.create_task(self._load())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
    
    async def add(
        self,
        episode_type: EpisodeType,
        agent_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        parent_id: Optional[str] = None,
        tags: Optional[Set[str]] = None,
        importance: float = 1.0,
        embedding: Optional[np.ndarray] = None,
        session_id: Optional[str] = None,
    ) -> Episode:
        """
        Add an episode to the timeline.
        
        Args:
            episode_type: Type of episodic event
            agent_id: ID of agent that generated this episode
            content: Text content of the episode
            metadata: Additional metadata
            parent_id: Parent episode ID for hierarchical structure
            tags: Tags for categorization
            importance: Importance weight (0.0 - 1.0)
            embedding: Optional pre-computed embedding
            session_id: Session identifier (uses current if not provided)
            
        Returns:
            Created Episode object
        """
        async with self._lock:
            episode_id = str(uuid.uuid4())
            timestamp = time.time()
            
            episode = Episode(
                id=episode_id,
                episode_type=episode_type,
                timestamp=timestamp,
                agent_id=agent_id,
                content=content,
                metadata=metadata or {},
                parent_id=parent_id,
                tags=tags or set(),
                importance=importance,
                embedding=embedding,
            )
            
            # Store episode
            self._episodes[episode_id] = episode
            self._chronological.append(episode_id)
            self._by_agent[agent_id].append(episode_id)
            self._by_type[episode_type].append(episode_id)
            
            for tag in episode.tags:
                self._by_tag[tag].append(episode_id)
            
            # Handle parent-child relationship
            if parent_id and parent_id in self._episodes:
                self._episodes[parent_id].children_ids.append(episode_id)
            
            # Session tracking
            session = session_id or self._current_session_id or "default"
            self._session_episodes[session].append(episode_id)
            
            # Update stats
            self._episode_count += 1
            self._stats["total_episodes"] += 1
            
            # Persist if needed
            if self.auto_persist and self._episode_count % self.persist_interval == 0:
                task = asyncio.create_task(self._persist())
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
            
            # Enforce max episodes (LRU eviction)
            await self._enforce_limit()
            
            # Index in vector store if available
            if self.vector_store and embedding is not None:
                await self.vector_store.add(
                    text=content,
                    vector=embedding,
                    metadata={
                        "episode_id": episode_id,
                        "episode_type": episode_type.value,
                        "agent_id": agent_id,
                        "timestamp": timestamp,
                        "importance": importance,
                    },
                )
            
            return episode
    
    async def query(self, query: EpisodicQuery) -> List[Episode]:
        """
        Query episodes with flexible filtering.
        
        Args:
            query: EpisodicQuery with filters
            
        Returns:
            List of matching episodes
        """
        start_time = time.perf_counter()
        
        async with self._lock:
            # Start with all episodes
            candidate_ids = set(self._chronological)
            
            # Apply filters
            if query.agent_id:
                candidate_ids &= set(self._by_agent.get(query.agent_id, []))
            
            if query.episode_types:
                type_ids = set()
                for ep_type in query.episode_types:
                    type_ids.update(self._by_type.get(ep_type, []))
                candidate_ids &= type_ids
            
            if query.tags:
                tag_ids = set()
                for tag in query.tags:
                    tag_ids.update(self._by_tag.get(tag, []))
                candidate_ids &= tag_ids
            
            if query.time_range:
                start_ts, end_ts = query.time_range
                time_ids = {
                    eid for eid in candidate_ids
                    if start_ts <= self._episodes[eid].timestamp <= end_ts
                }
                candidate_ids = time_ids
            
            if query.min_importance > 0:
                candidate_ids = {
                    eid for eid in candidate_ids
                    if self._episodes[eid].importance >= query.min_importance
                }
            
            # Semantic search if query_text provided and vector store available
            if query.query_text and self.vector_store:
                self._stats["semantic_queries"] += 1
                results = await self.vector_store.search_by_text(
                    query_text=query.query_text,
                    embed_fn=lambda t: np.zeros(768),  # placeholder - needs real embed fn
                    top_k=query.limit * 2,
                    threshold=query.similarity_threshold,
                )
                semantic_ids = {r.entry.metadata.get("episode_id") for r in results if r.entry.metadata.get("episode_id")}
                candidate_ids &= semantic_ids
            else:
                self._stats["temporal_queries"] += 1
            
            # Sort by timestamp
            episodes = [self._episodes[eid] for eid in candidate_ids if eid in self._episodes]
            episodes.sort(key=lambda e: e.timestamp, reverse=query.reverse_chronological)
            
            # Apply limit
            episodes = episodes[:query.limit]
            
            # Update stats
            latency_ms = (time.perf_counter() - start_time) * 1000
            self._stats["total_queries"] += 1
            self._stats["avg_query_latency_ms"] = (
                (self._stats["avg_query_latency_ms"] * (self._stats["total_queries"] - 1) + latency_ms)
                / self._stats["total_queries"]
            )
            
            return episodes
    
    async def get_episode(self, episode_id: str) -> Optional[Episode]:
        """Get episode by ID."""
        async with self._lock:
            return self._episodes.get(episode_id)
    
    async def get_session_episodes(self, session_id: str) -> List[Episode]:
        """Get all episodes for a session in chronological order."""
        async with self._lock:
            episode_ids = self._session_episodes.get(session_id, [])
            return [self._episodes[eid] for eid in episode_ids if eid in self._episodes]
    
    async def get_agent_timeline(
        self,
        agent_id: str,
        limit: int = 100,
        episode_types: Optional[List[EpisodeType]] = None,
    ) -> List[Episode]:
        """Get chronological timeline for a specific agent."""
        query = EpisodicQuery(
            agent_id=agent_id,
            episode_types=episode_types,
            limit=limit,
            reverse_chronological=True,
        )
        return await self.query(query)
    
    async def get_conversation_thread(
        self,
        root_episode_id: str,
        max_depth: int = 10,
    ) -> List[Episode]:
        """Get conversation thread starting from root episode."""
        async with self._lock:
            thread = []
            visited = set()
            
            def traverse(ep_id: str, depth: int):
                if depth > max_depth or ep_id in visited or ep_id not in self._episodes:
                    return
                visited.add(ep_id)
                episode = self._episodes[ep_id]
                thread.append(episode)
                for child_id in episode.children_ids:
                    traverse(child_id, depth + 1)
            
            traverse(root_episode_id, 0)
            thread.sort(key=lambda e: e.timestamp)
            return thread
    
    async def add_tags(self, episode_id: str, tags: Set[str]) -> bool:
        """Add tags to an episode."""
        async with self._lock:
            if episode_id not in self._episodes:
                return False
            
            episode = self._episodes[episode_id]
            for tag in tags:
                if tag not in episode.tags:
                    episode.tags.add(tag)
                    self._by_tag[tag].append(episode_id)
            return True
    
    async def set_importance(self, episode_id: str, importance: float) -> bool:
        """Update episode importance."""
        async with self._lock:
            if episode_id in self._episodes:
                self._episodes[episode_id].importance = max(0.0, min(1.0, importance))
                return True
            return False
    
    async def _enforce_limit(self) -> None:
        """Enforce max episodes limit by removing oldest low-importance episodes."""
        if len(self._episodes) <= self.max_episodes:
            return
        
        # Sort by importance then timestamp, remove lowest
        sorted_ids = sorted(
            self._chronological,
            key=lambda eid: (self._episodes[eid].importance, self._episodes[eid].timestamp)
        )
        
        to_remove = len(self._episodes) - self.max_episodes
        for eid in sorted_ids[:to_remove]:
            await self._remove_episode(eid)
    
    async def _remove_episode(self, episode_id: str) -> None:
        """Remove episode and clean up indexes."""
        if episode_id not in self._episodes:
            return
        
        episode = self._episodes[episode_id]
        
        # Remove from indexes
        if episode_id in self._by_agent[episode.agent_id]:
            self._by_agent[episode.agent_id].remove(episode_id)
        
        if episode_id in self._by_type[episode.episode_type]:
            self._by_type[episode.episode_type].remove(episode_id)
        
        for tag in episode.tags:
            if episode_id in self._by_tag[tag]:
                self._by_tag[tag].remove(episode_id)
        
        if episode_id in self._chronological:
            self._chronological.remove(episode_id)
        
        for session_ids in self._session_episodes.values():
            if episode_id in session_ids:
                session_ids.remove(episode_id)
        
        # Remove parent reference
        if episode.parent_id and episode.parent_id in self._episodes:
            parent = self._episodes[episode.parent_id]
            if episode_id in parent.children_ids:
                parent.children_ids.remove(episode_id)
        
        del self._episodes[episode_id]
    
    async def _persist(self) -> None:
        """Persist episodes to disk."""
        if not self.persistence_path:
            return
        
        try:
            data = {
                "episodes": {eid: ep.to_dict() for eid, ep in self._episodes.items()},
                "chronological": self._chronological,
                "by_agent": dict(self._by_agent),
                "by_type": {k.value: v for k, v in self._by_type.items()},
                "by_tag": dict(self._by_tag),
                "session_episodes": dict(self._session_episodes),
                "current_session": self._current_session_id,
                "stats": self._stats,
            }
            
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.persistence_path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Failed to persist episodic store: {e}")
    
    async def _load(self) -> None:
        """Load episodes from disk."""
        if not self.persistence_path or not self.persistence_path.exists():
            return
        
        try:
            with open(self.persistence_path, "r") as f:
                data = json.load(f)
            
            for eid, ep_data in data.get("episodes", {}).items():
                episode = Episode.from_dict(ep_data)
                self._episodes[eid] = episode
            
            self._chronological = data.get("chronological", [])
            self._by_agent = defaultdict(list, data.get("by_agent", {}))
            self._by_type = defaultdict(list, {
                EpisodeType(k): v for k, v in data.get("by_type", {}).items()
            })
            self._by_tag = defaultdict(list, data.get("by_tag", {}))
            self._session_episodes = defaultdict(list, data.get("session_episodes", {}))
            self._current_session_id = data.get("current_session")
            self._stats = data.get("stats", self._stats)
            self._episode_count = len(self._episodes)
            
        except Exception as e:
            print(f"Failed to load episodic store: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get store statistics."""
        return {
            **self._stats,
            "total_episodes_in_memory": len(self._episodes),
            "max_episodes": self.max_episodes,
            "unique_agents": len(self._by_agent),
            "unique_tags": len(self._by_tag),
            "sessions": len(self._session_episodes),
        }
    
    async def clear(self) -> None:
        """Clear all episodes."""
        async with self._lock:
            self._episodes.clear()
            self._by_agent.clear()
            self._by_type.clear()
            self._by_tag.clear()
            self._chronological.clear()
            self._session_episodes.clear()
            self._episode_count = 0