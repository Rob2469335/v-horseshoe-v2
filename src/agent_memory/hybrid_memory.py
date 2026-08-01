"""
Hybrid Memory - Unified Interface for Multi-Modal Agent Memory.

Combines:
- Vector Store (Semantic Recall)
- Episodic Store (Temporal/Event History)  
- Working Memory (Active Context - LRU)

Inspired by LangGraph's memory patterns and AutoGen's conversation history structures.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np

from .vector_store import VectorStore, VectorEntry, SearchResult, SimilarityMetric
from .episodic_store import EpisodicStore, Episode, EpisodeType, EpisodicQuery
from .working_memory import LRUWorkingMemory as WorkingMemory, WorkingMemoryEntry, StringWorkingMemory
from ._memory_config import MemoryConfig, MemoryContext

__all__ = ["HybridMemory", "MemoryConfig", "MemoryContext"]


class HybridMemory:
    """
    Unified Hybrid Memory System for AI Agents.
    
    Provides a single interface combining:
    - Semantic Vector Memory (what is this about?)
    - Episodic Timeline Memory (what happened when?)
    - Working Memory (what's happening now?)
    
    Usage:
        memory = HybridMemory(config)
        await memory.initialize(embed_fn=my_embedding_function)
        
        # Store
        await memory.remember(
            agent_id="agent_1",
            content="User asked about Python async patterns",
            episode_type=EpisodeType.USER_MESSAGE,
            metadata={"topic": "python", "async": True}
        )
        
        # Recall
        context = await memory.recall(
            agent_id="agent_1",
            query="Python async best practices"
        )
        prompt_context = context.get_combined_context()
    """
    
    def __init__(self, config: Optional[MemoryConfig] = None):
        self.config = config or MemoryConfig()
        
        # Initialize sub-stores
        self.vector_store = VectorStore(
            dimension=self.config.vector_dimension,
            similarity_threshold=self.config.vector_similarity_threshold,
            cache_size=self.config.vector_cache_size,
            similarity_metric=self.config.vector_similarity_metric,
        )
        
        episodic_persist = None
        if self.config.episodic_persist_path:
            episodic_persist = Path(self.config.episodic_persist_path)
        
        self.episodic_store = EpisodicStore(
            vector_store=self.vector_store if self.config.auto_embed_episodes else None,
            max_episodes=self.config.episodic_max_episodes,
            persistence_path=episodic_persist,
            auto_persist=self.config.episodic_auto_persist,
            persist_interval=self.config.episodic_persist_interval,
        )
        
        working_persist = None
        if self.config.working_persist_path:
            working_persist = Path(self.config.working_persist_path)
        
        self.working_memory = StringWorkingMemory(
            max_size=self.config.working_max_size,
            default_ttl=self.config.working_ttl_seconds,
            persistence_path=working_persist,
            enable_persistence=self.config.working_enable_persistence,
        )
        
        self._embed_fn = self.config.embed_fn
        self._lock = asyncio.Lock()

        # Fire-and-forget task tracking (strong refs prevent GC mid-await)
        self._bg_tasks: Set[asyncio.Task] = set()
        
        # Session tracking
        self._current_session_id: Optional[str] = None
        self._agent_sessions: Dict[str, str] = {}  # agent_id -> session_id
        
        # Stats
        self._stats = {
            "total_remembers": 0,
            "total_recalls": 0,
            "avg_recall_latency_ms": 0.0,
            "vector_queries": 0,
            "episodic_queries": 0,
            "working_queries": 0,
        }
    
    async def initialize(self, embed_fn: Optional[callable] = None) -> None:
        """Initialize memory with embedding function."""
        if embed_fn:
            self._embed_fn = embed_fn
            self.config.embed_fn = embed_fn
        
        # Load persisted data
        await self.working_memory.load()
        
        # Start episodic persistence task if enabled
        if self.config.episodic_auto_persist and self.config.episodic_persist_path:
            task = asyncio.create_task(self._persist_loop())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
    
    def set_embed_fn(self, embed_fn: callable) -> None:
        """Set embedding function after initialization."""
        self._embed_fn = embed_fn
    
    def start_session(self, session_id: Optional[str] = None) -> str:
        """Start a new memory session."""
        if session_id is None:
            session_id = str(uuid.uuid4())
        self._current_session_id = session_id
        return session_id
    
    def get_session_id(self, agent_id: Optional[str] = None) -> str:
        """Get current session ID for agent."""
        if agent_id and agent_id in self._agent_sessions:
            return self._agent_sessions[agent_id]
        return self._current_session_id or "default"
    
    async def remember(
        self,
        agent_id: str,
        content: str,
        episode_type: EpisodeType = EpisodeType.AGENT_MESSAGE,
        metadata: Optional[Dict[str, Any]] = None,
        importance: float = 1.0,
        tags: Optional[Set[str]] = None,
        parent_episode_id: Optional[str] = None,
        session_id: Optional[str] = None,
        add_to_working: bool = True,
        working_key: Optional[str] = None,
    ) -> Episode:
        """
        Store a memory across all three layers.
        
        Args:
            agent_id: ID of the agent
            content: Text content to remember
            episode_type: Type of episodic event
            metadata: Additional metadata
            importance: Importance weight (0.0 - 1.0)
            tags: Tags for categorization
            parent_episode_id: Parent episode for threading
            session_id: Session identifier
            add_to_working: Also add to working memory
            working_key: Custom key for working memory
            
        Returns:
            Created Episode
        """
        start_time = time.perf_counter()
        session = session_id or self.get_session_id(agent_id)
        
        # Generate embedding if function available
        embedding = None
        if self._embed_fn:
            try:
                embedding = await self._embed_fn(content)
            except Exception as e:
                # Log but continue without embedding
                pass
        
        # Add to episodic store (primary)
        episode = await self.episodic_store.add(
            episode_type=episode_type,
            agent_id=agent_id,
            content=content,
            metadata=metadata or {},
            parent_id=parent_episode_id,
            tags=tags or set(),
            importance=importance,
            embedding=embedding,
            session_id=session,
        )
        
        # Add to vector store for semantic search
        if embedding is not None:
            await self.vector_store.add(
                text=content,
                vector=embedding,
                metadata={
                    "episode_id": episode.id,
                    "episode_type": episode_type.value,
                    "agent_id": agent_id,
                    "session_id": session,
                    "importance": importance,
                    **(metadata or {}),
                },
            )
        
        # Add to working memory for active context
        if add_to_working:
            # Map episode type to role for conversation context
            role_map = {
                EpisodeType.USER_MESSAGE.value: "user",
                EpisodeType.AGENT_MESSAGE.value: "assistant",
                EpisodeType.SYSTEM_EVENT.value: "system",
                EpisodeType.TASK_START.value: "user",
                EpisodeType.TASK_COMPLETE.value: "assistant",
            }
            role = role_map.get(episode_type.value, "unknown")
            
            wm_key = working_key or f"{episode_type.value}_{int(time.time() * 1000)}"
            await self.working_memory.put(
                key=wm_key,
                value=content,
                metadata={
                    "episode_id": episode.id,
                    "episode_type": episode_type.value,
                    "agent_id": agent_id,
                    "session_id": session,
                    "role": role,
                    **(metadata or {}),
                },
                importance=importance,
                tags=tags or set(),
            )
        
        # Update agent session
        self._agent_sessions[agent_id] = session
        
        # Stats
        latency_ms = (time.perf_counter() - start_time) * 1000
        self._stats["total_remembers"] += 1
        
        return episode
    
    async def recall(
        self,
        agent_id: str,
        query: str,
        top_k: Optional[int] = None,
        episode_types: Optional[List[EpisodeType]] = None,
        time_range: Optional[tuple] = None,
        tags: Optional[Set[str]] = None,
        min_importance: float = 0.0,
        include_working: bool = True,
        include_episodic: bool = True,
        include_semantic: bool = True,
        session_id: Optional[str] = None,
    ) -> MemoryContext:
        """
        Recall memory across all three layers.
        
        Args:
            agent_id: Agent identifier
            query: Search query text
            top_k: Max semantic results
            episode_types: Filter episodic types
            time_range: (start, end) timestamp filter
            tags: Tag filters
            min_importance: Minimum importance
            include_working: Include working memory
            include_episodic: Include episodic memory
            include_semantic: Include semantic search
            session_id: Specific session to query
            
        Returns:
            MemoryContext with combined results
        """
        start_time = time.perf_counter()
        session = session_id or self.get_session_id(agent_id)
        
        context = MemoryContext(
            query=query,
            agent_id=agent_id,
            timestamp=time.time(),
        )
        
        # Parallel retrieval from all stores
        tasks = []
        
        if include_working:
            tasks.append(self._recall_working(agent_id, session, context))
        
        if include_episodic:
            tasks.append(self._recall_episodic(agent_id, session, query, 
                                               episode_types, time_range, tags, 
                                               min_importance, context))
        
        if include_semantic and self._embed_fn:
            tasks.append(self._recall_semantic(query, top_k, context))
        
        await asyncio.gather(*tasks)
        
        # Also get agent timeline
        context.agent_timeline = await self.episodic_store.get_agent_timeline(
            agent_id=agent_id,
            limit=20,
        )
        
        # Update latency stats
        total_latency = (time.perf_counter() - start_time) * 1000
        context.total_latency_ms = total_latency
        self._stats["total_recalls"] += 1
        self._stats["avg_recall_latency_ms"] = (
            (self._stats["avg_recall_latency_ms"] * (self._stats["total_recalls"] - 1) + total_latency)
            / self._stats["total_recalls"]
        )
        
        return context
    
    async def _recall_working(
        self,
        agent_id: str,
        session: str,
        context: MemoryContext,
    ) -> None:
        """Recall from working memory."""
        self._stats["working_queries"] += 1
        
        # Get sliding window (recent conversation)
        context.conversation_window = await self.working_memory.get_conversation_window(
            window_size=self.config.working_window_size,
            partition_key=session,
        )
        
        # Get recent working entries
        context.working_entries = await self.working_memory.get_recent(
            partition_key=session,
            limit=20,
            min_importance=0.3,
        )
    
    async def _recall_episodic(
        self,
        agent_id: str,
        session: str,
        query: str,
        episode_types: Optional[List[EpisodeType]],
        time_range: Optional[tuple],
        tags: Optional[Set[str]],
        min_importance: float,
        context: MemoryContext,
    ) -> None:
        """Recall from episodic store."""
        self._stats["episodic_queries"] += 1
        
        # Get session episodes
        session_episodes = await self.episodic_store.get_session_episodes(session)
        context.episodes = session_episodes[-self.config.episodic_recall_limit:]
        
        # Also query with filters
        ep_query = EpisodicQuery(
            agent_id=agent_id,
            episode_types=episode_types,
            time_range=time_range,
            tags=tags,
            min_importance=min_importance,
            limit=self.config.episodic_recall_limit,
            query_text=query if self._embed_fn else None,
            similarity_threshold=self.config.vector_similarity_threshold,
        )
        filtered_episodes = await self.episodic_store.query(ep_query)
        # Merge with session episodes (deduplicate)
        seen = {ep.id for ep in context.episodes}
        for ep in filtered_episodes:
            if ep.id not in seen:
                context.episodes.append(ep)
                seen.add(ep.id)
        
        # Sort by recency
        context.episodes.sort(key=lambda e: e.timestamp, reverse=True)
        context.episodes = context.episodes[:self.config.episodic_recall_limit]
    
    async def _recall_semantic(
        self,
        query: str,
        top_k: Optional[int],
        context: MemoryContext,
    ) -> None:
        """Recall from vector store (semantic search)."""
        self._stats["vector_queries"] += 1
        
        if not self._embed_fn:
            return
        
        k = top_k or self.config.semantic_recall_top_k
        results = await self.vector_store.search_by_text(
            query_text=query,
            embed_fn=self._embed_fn,
            top_k=k,
            threshold=self.config.vector_similarity_threshold,
        )
        context.semantic_results = results
    
    async def get_conversation_context(
        self,
        agent_id: str,
        window_size: int = 20,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Get recent conversation window for LLM context."""
        session = session_id or self.get_session_id(agent_id)
        return await self.working_memory.get_conversation_window(
            window_size=window_size,
            partition_key=session,
        )
    
    async def get_agent_timeline(
        self,
        agent_id: str,
        limit: int = 50,
        episode_types: Optional[List[EpisodeType]] = None,
    ) -> List[Episode]:
        """Get chronological timeline for agent."""
        return await self.episodic_store.get_agent_timeline(
            agent_id=agent_id,
            limit=limit,
            episode_types=episode_types,
        )
    
    async def get_conversation_thread(
        self,
        root_episode_id: str,
        max_depth: int = 10,
    ) -> List[Episode]:
        """Get conversation thread from root episode."""
        return await self.episodic_store.get_conversation_thread(
            root_episode_id=root_episode_id,
            max_depth=max_depth,
        )
    
    async def update_working_importance(
        self,
        key: str,
        importance: float,
        partition_key: Optional[str] = None,
    ) -> bool:
        """Update importance of working memory entry."""
        return await self.working_memory.update_importance(
            key=key,
            importance=importance,
            partition_key=partition_key,
        )
    
    async def add_working_tags(
        self,
        key: str,
        tags: Set[str],
        partition_key: Optional[str] = None,
    ) -> bool:
        """Add tags to working memory entry."""
        return await self.working_memory.add_tags(
            key=key,
            tags=tags,
            partition_key=partition_key,
        )
    
    async def consolidate_memories(self) -> Dict[str, int]:
        """
        Consolidate memories - merge similar episodes, promote important working
        memories to episodic, etc.
        
        Returns:
            Stats about consolidation
        """
        stats = {"consolidated": 0, "promoted": 0, "archived": 0}
        
        # Consolidate vector store
        if hasattr(self.vector_store, 'consolidate'):
            consolidated = await self.vector_store.consolidate()
            stats["consolidated"] = consolidated
        
        # Consolidate episodic store
        if hasattr(self.episodic_store, 'consolidate_memories'):
            consolidated = await self.episodic_store.consolidate_memories()
            if consolidated:
                stats["consolidated"] += 1
        
        # Promote high-importance working memories to episodic
        for partition_key in self.working_memory.get_partition_keys():
            recent = await self.working_memory.get_recent(
                partition_key=partition_key,
                limit=50,
                min_importance=0.8,
            )
            for entry in recent:
                if entry.metadata.get("promoted"):
                    continue
                
                await self.remember(
                    agent_id=entry.metadata.get("agent_id", "unknown"),
                    content=entry.value,
                    episode_type=EpisodeType.CHECKPOINT,
                    metadata={**entry.metadata, "promoted_from_working": True},
                    importance=entry.importance,
                    tags=entry.tags,
                    add_to_working=False,
                )
                entry.metadata["promoted"] = True
                stats["promoted"] += 1
        
        return stats
    
    async def _persist_loop(self) -> None:
        """Background persistence loop."""
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            try:
                await self.working_memory.persist()
            except Exception:
                pass  # Silently fail, will retry
    
    async def persist_all(self) -> bool:
        """Persist all memory stores."""
        results = await asyncio.gather(
            self.working_memory.persist(),
            return_exceptions=True,
        )
        return all(r is True for r in results)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive memory statistics."""
        return {
            "hybrid": self._stats,
            "vector_store": self.vector_store.get_stats(),
            "episodic_store": {
                "total_episodes": self.episodic_store._episode_count,
                "by_agent": {k: len(v) for k, v in self.episodic_store._by_agent.items()},
                "by_type": {k.value: len(v) for k, v in self.episodic_store._by_type.items()},
                "sessions": len(self.episodic_store._session_episodes),
            },
            "working_memory": self.working_memory.get_stats(),
            "current_session": self._current_session_id,
            "agent_sessions": dict(self._agent_sessions),
        }
    
    async def clear_agent_memory(self, agent_id: str) -> Dict[str, int]:
        """Clear all memory for a specific agent."""
        # Clear working memory partitions for agent
        wm_cleared = 0
        for pk in list(self.working_memory.get_partition_keys()):
            if pk.startswith(f"agent_{agent_id}") or pk == agent_id:
                wm_cleared += await self.working_memory.clear_partition(pk)
        
        # Clear episodic memory for agent
        ep_cleared = 0
        async with self.episodic_store._lock:
            agent_eps = self.episodic_store._by_agent.get(agent_id, [])
            for ep_id in agent_eps:
                if ep_id in self.episodic_store._episodes:
                    del self.episodic_store._episodes[ep_id]
                    ep_cleared += 1
            self.episodic_store._by_agent[agent_id] = []
        
        # Clear vector store entries for agent
        vec_cleared = 0
        # Note: Vector store doesn't have agent index, would need scan
        
        return {
            "working_memory": wm_cleared,
            "episodic": ep_cleared,
            "vector": vec_cleared,
        }
    
    async def close(self) -> None:
        """Cleanup and persist."""
        await self.persist_all()
        for task in list(self._bg_tasks):
            task.cancel()
        self._bg_tasks.clear()


# Backward compatibility aliases
HybridAgentMemory = HybridMemory
AgentMemoryConfig = MemoryConfig
AgentMemoryContext = MemoryContext