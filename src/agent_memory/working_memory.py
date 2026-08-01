"""
LRU Working Memory for Short-term Context Retention.

LangGraph-inspired working memory with:
- LRU eviction policy
- Sliding window for conversation context
- Thread/agent-scoped memory partitions
- TTL-based expiration
- Importance-weighted retention
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set, Generic, TypeVar

import numpy as np
from scipy.spatial.distance import cosine

from ._working_memory_entry import WorkingMemoryEntry

logger = logging.getLogger(__name__)

T = TypeVar('T')


class LRUWorkingMemory(Generic[T]):
    """
    Thread-safe LRU working memory with importance-weighted eviction.
    
    Features:
    - LRU eviction with importance weighting
    - Per-agent/thread memory partitions
    - TTL-based expiration
    - Tag-based retrieval
    - Sliding window for conversation context
    - Configurable capacity limits
    """
    
    def __init__(
        self,
        max_size: int = 1000,
        default_ttl: Optional[float] = 3600.0,  # 1 hour default
        partition_by: Optional[str] = None,  # "agent_id", "thread_id", None
        enable_persistence: bool = False,
        persistence_path: Optional[str] = None,
    ):
        """
        Initialize working memory.
        
        Args:
            max_size: Maximum entries per partition (or total if no partitioning)
            default_ttl: Default time-to-live in seconds (None = no expiry)
            partition_by: Partition strategy - "agent_id", "thread_id", or None
            enable_persistence: Enable disk persistence
            persistence_path: Path for persistence file
        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.partition_by = partition_by
        self.enable_persistence = enable_persistence
        self.persistence_path = persistence_path
        
        # Main storage: partition_key -> OrderedDict[key, entry]
        # OrderedDict maintains LRU order (first = oldest, last = newest)
        self._partitions: Dict[str, OrderedDict[str, WorkingMemoryEntry[T]]] = {}
        
        # Global tag index: tag -> Set[(partition_key, entry_key)]
        self._tag_index: Dict[str, Set[tuple]] = {}
        
        # Stats
        self._stats = {
            "total_puts": 0,
            "total_gets": 0,
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "expirations": 0,
        }
        
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
    
    def _get_partition_key(self, metadata: Dict[str, Any]) -> str:
        """Extract partition key from metadata."""
        if self.partition_by and self.partition_by in metadata:
            return str(metadata[self.partition_by])
        return "default"
    
    def _get_partition(self, partition_key: str) -> OrderedDict[str, WorkingMemoryEntry[T]]:
        """Get or create partition."""
        if partition_key not in self._partitions:
            self._partitions[partition_key] = OrderedDict()
        return self._partitions[partition_key]
    
    def _update_tag_index(self, partition_key: str, entry_key: str, tags: Set[str], add: bool = True) -> None:
        """Update tag index."""
        for tag in tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = set()
            
            key = (partition_key, entry_key)
            if add:
                self._tag_index[tag].add(key)
            else:
                self._tag_index[tag].discard(key)
            
            if not self._tag_index[tag]:
                del self._tag_index[tag]
    
    def _evict_if_needed(self, partition: OrderedDict[str, WorkingMemoryEntry[T]]) -> None:
        """Evict entries if partition exceeds max_size."""
        while len(partition) > self.max_size:
            # Find entry with lowest effective priority
            min_priority = float('inf')
            evict_key = None
            
            for key, entry in partition.items():
                if entry.is_expired():
                    evict_key = key
                    break
                priority = entry.effective_priority()
                if priority < min_priority:
                    min_priority = priority
                    evict_key = key
            
            if evict_key:
                entry = partition.pop(evict_key)
                self._update_tag_index(entry_key=evict_key, partition_key=list(self._partitions.keys())[
                    list(self._partitions.values()).index(partition)], tags=entry.tags, add=False)
                self._stats["evictions"] += 1
                logger.debug(f"Evicted entry {evict_key} (priority: {min_priority:.4f})")
            else:
                break
    
    async def start_cleanup_task(self, interval: float = 60.0) -> None:
        """Start background cleanup task for expired entries."""
        if self._running:
            return
        
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(interval))
        logger.info("Working memory cleanup task started")
    
    async def stop_cleanup_task(self) -> None:
        """Stop background cleanup task."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("Working memory cleanup task stopped")
    
    async def _cleanup_loop(self, interval: float) -> None:
        """Background loop to remove expired entries."""
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
    
    async def _cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        removed = 0
        async with self._lock:
            for partition_key, partition in list(self._partitions.items()):
                expired_keys = [
                    key for key, entry in partition.items()
                    if entry.is_expired()
                ]
                for key in expired_keys:
                    entry = partition.pop(key)
                    self._update_tag_index(partition_key, key, entry.tags, add=False)
                    removed += 1
                    self._stats["expirations"] += 1
        
        if removed > 0:
            logger.debug(f"Cleaned up {removed} expired entries")
        return removed
    
    async def put(
        self,
        key: str,
        value: T,
        metadata: Optional[Dict[str, Any]] = None,
        importance: float = 1.0,
        ttl: Optional[float] = None,
        tags: Optional[Set[str]] = None,
        partition_key: Optional[str] = None,
    ) -> bool:
        """
        Store a value in working memory.
        
        Args:
            key: Unique key within partition
            value: Value to store
            metadata: Additional metadata (used for partitioning)
            importance: Importance weight 0.0-1.0
            ttl: Time-to-live in seconds (overrides default)
            tags: Set of tags for retrieval
            partition_key: Explicit partition key (overrides metadata)
            
        Returns:
            True if stored successfully
        """
        metadata = metadata or {}
        tags = tags or set()
        ttl = ttl if ttl is not None else self.default_ttl
        
        # Determine partition
        if partition_key is None:
            partition_key = self._get_partition_key(metadata)
        
        async with self._lock:
            partition = self._get_partition(partition_key)
            
            # Create entry
            entry = WorkingMemoryEntry(
                key=key,
                value=value,
                metadata=metadata,
                importance=max(0.0, min(1.0, importance)),
                ttl=ttl,
                tags=tags,
            )
            
            # Remove existing if present
            if key in partition:
                old_entry = partition[key]
                self._update_tag_index(partition_key, key, old_entry.tags, add=False)
            
            # Add to partition (moves to end = most recent)
            partition[key] = entry
            
            # Update tag index
            self._update_tag_index(partition_key, key, tags, add=True)
            
            # Evict if needed
            self._evict_if_needed(partition)
            
            self._stats["total_puts"] += 1
            return True
    
    async def get(
        self,
        key: str,
        partition_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[T]:
        """
        Retrieve a value from working memory.
        
        Args:
            key: Entry key
            partition_key: Explicit partition
            metadata: For partition inference if partition_key not provided
            
        Returns:
            Value if found and not expired, None otherwise
        """
        if partition_key is None:
            partition_key = self._get_partition_key(metadata or {})
        
        async with self._lock:
            partition = self._partitions.get(partition_key)
            if not partition or key not in partition:
                self._stats["total_gets"] += 1
                self._stats["misses"] += 1
                return None
            
            entry = partition[key]
            
            # Check expiration
            if entry.is_expired():
                partition.pop(key)
                self._update_tag_index(partition_key, key, entry.tags, add=False)
                self._stats["total_gets"] += 1
                self._stats["misses"] += 1
                self._stats["expirations"] += 1
                return None
            
            # Move to end (most recently used)
            partition.move_to_end(key)
            entry.touch()
            
            self._stats["total_gets"] += 1
            self._stats["hits"] += 1
            return entry.value
    
    async def get_entry(
        self,
        key: str,
        partition_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[WorkingMemoryEntry[T]]:
        """Get full entry with metadata."""
        if partition_key is None:
            partition_key = self._get_partition_key(metadata or {})
        
        async with self._lock:
            partition = self._partitions.get(partition_key)
            if not partition or key not in partition:
                return None
            
            entry = partition[key]
            if entry.is_expired():
                partition.pop(key)
                self._update_tag_index(partition_key, key, entry.tags, add=False)
                return None
            
            partition.move_to_end(key)
            entry.touch()
            return entry
    
    async def delete(
        self,
        key: str,
        partition_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Delete an entry."""
        if partition_key is None:
            partition_key = self._get_partition_key(metadata or {})
        
        async with self._lock:
            partition = self._partitions.get(partition_key)
            if not partition or key not in partition:
                return False
            
            entry = partition.pop(key)
            self._update_tag_index(partition_key, key, entry.tags, add=False)
            return True
    
    async def get_by_tags(
        self,
        tags: Set[str],
        match_all: bool = True,
        partition_key: Optional[str] = None,
        limit: int = 100,
    ) -> List[WorkingMemoryEntry[T]]:
        """
        Retrieve entries by tags.
        
        Args:
            tags: Tags to match
            match_all: If True, match all tags (AND). If False, match any (OR).
            partition_key: Limit to specific partition
            limit: Maximum results
            
        Returns:
            List of matching entries (most recent first)
        """
        async with self._lock:
            # Find candidate keys
            if match_all:
                candidate_sets = [self._tag_index.get(tag, set()) for tag in tags]
                if not candidate_sets:
                    return []
                candidates = set.intersection(*candidate_sets)
            else:
                candidates = set()
                for tag in tags:
                    candidates.update(self._tag_index.get(tag, set()))
            
            # Filter by partition if specified
            if partition_key:
                candidates = {(pk, ek) for pk, ek in candidates if pk == partition_key}
            
            # Collect entries
            results = []
            for pk, ek in candidates:
                partition = self._partitions.get(pk)
                if partition and ek in partition:
                    entry = partition[ek]
                    if not entry.is_expired():
                        results.append(entry)
            
            # Sort by recency (most recent first)
            results.sort(key=lambda e: e.last_accessed, reverse=True)
            return results[:limit]
    
    async def get_recent(
        self,
        partition_key: Optional[str] = None,
        limit: int = 10,
        min_importance: float = 0.0,
    ) -> List[WorkingMemoryEntry[T]]:
        """Get most recent entries in a partition."""
        if partition_key is None:
            partition_key = "default"
        
        async with self._lock:
            partition = self._partitions.get(partition_key)
            if not partition:
                return []
            
            # Get entries from end (most recent)
            entries = list(partition.values())[-limit:]
            entries = [e for e in entries if not e.is_expired() and e.importance >= min_importance]
            entries.reverse()  # Most recent first
            return entries
    
    async def get_sliding_window(
        self,
        partition_key: Optional[str] = None,
        window_size: int = 20,
        max_age_seconds: Optional[float] = None,
    ) -> List[WorkingMemoryEntry[T]]:
        """
        Get sliding window of recent entries (for conversation context).
        
        Args:
            partition_key: Partition to query
            window_size: Maximum number of entries
            max_age_seconds: Maximum age of entries
            
        Returns:
            List of entries in chronological order (oldest first)
        """
        if partition_key is None:
            partition_key = "default"
        
        async with self._lock:
            partition = self._partitions.get(partition_key)
            if not partition:
                return []
            
            now = time.time()
            entries = []
            
            # Iterate from most recent
            for entry in reversed(partition.values()):
                if entry.is_expired():
                    continue
                if max_age_seconds and (now - entry.created_at) > max_age_seconds:
                    continue
                entries.append(entry)
                if len(entries) >= window_size:
                    break
            
            # Return chronological order
            entries.reverse()
            return entries
    
    async def update_importance(
        self,
        key: str,
        importance: float,
        partition_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update importance of an entry."""
        if partition_key is None:
            partition_key = self._get_partition_key(metadata or {})
        
        async with self._lock:
            partition = self._partitions.get(partition_key)
            if not partition or key not in partition:
                return False
            
            partition[key].importance = max(0.0, min(1.0, importance))
            return True
    
    async def add_tags(
        self,
        key: str,
        tags: Set[str],
        partition_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Add tags to an entry."""
        if partition_key is None:
            partition_key = self._get_partition_key(metadata or {})
        
        async with self._lock:
            partition = self._partitions.get(partition_key)
            if not partition or key not in partition:
                return False
            
            entry = partition[key]
            new_tags = tags - entry.tags
            if new_tags:
                entry.tags.update(new_tags)
                self._update_tag_index(partition_key, key, new_tags, add=True)
            return True
    
    async def clear_partition(self, partition_key: str) -> int:
        """Clear all entries in a partition."""
        async with self._lock:
            partition = self._partitions.pop(partition_key, None)
            if not partition:
                return 0
            
            count = len(partition)
            for key, entry in partition.items():
                self._update_tag_index(partition_key, key, entry.tags, add=False)
            return count
    
    async def clear_all(self) -> int:
        """Clear all partitions."""
        async with self._lock:
            total = sum(len(p) for p in self._partitions.values())
            self._partitions.clear()
            self._tag_index.clear()
            return total
    
    def get_partition_keys(self) -> List[str]:
        """Get all partition keys."""
        return list(self._partitions.keys())
    
    def get_partition_size(self, partition_key: str) -> int:
        """Get size of a partition."""
        partition = self._partitions.get(partition_key)
        return len(partition) if partition else 0
    
    def get_total_size(self) -> int:
        """Get total entries across all partitions."""
        return sum(len(p) for p in self._partitions.values())
    
    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        total_gets = self._stats["total_gets"]
        hit_rate = self._stats["hits"] / total_gets if total_gets > 0 else 0.0
        
        return {
            **self._stats,
            "hit_rate": hit_rate,
            "total_partitions": len(self._partitions),
            "total_entries": self.get_total_size(),
            "max_size_per_partition": self.max_size,
            "tag_index_size": len(self._tag_index),
        }
    
    async def persist(self) -> bool:
        """Persist working memory to disk."""
        if not self.enable_persistence or not self.persistence_path:
            return False
        
        try:
            import json
            import os
            
            data = {
                "partitions": {},
                "stats": self._stats,
            }
            
            for pk, partition in self._partitions.items():
                data["partitions"][pk] = {}
                for key, entry in partition.items():
                    if not entry.is_expired():
                        data["partitions"][pk][key] = {
                            "value": entry.value,
                            "metadata": entry.metadata,
                            "importance": entry.importance,
                            "created_at": entry.created_at,
                            "last_accessed": entry.last_accessed,
                            "access_count": entry.access_count,
                            "ttl": entry.ttl,
                            "tags": list(entry.tags),
                        }
            
            os.makedirs(os.path.dirname(self.persistence_path), exist_ok=True)
            with open(self.persistence_path, "w") as f:
                json.dump(data, f)
            
            logger.info(f"Persisted working memory to {self.persistence_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to persist working memory: {e}")
            return False
    
    async def load(self) -> bool:
        """Load working memory from disk."""
        if not self.enable_persistence or not self.persistence_path:
            return False
        
        try:
            import json
            import os
            
            if not os.path.exists(self.persistence_path):
                return False
            
            with open(self.persistence_path, "r") as f:
                data = json.load(f)
            
            async with self._lock:
                self._partitions.clear()
                self._tag_index.clear()
                
                for pk, partition_data in data.get("partitions", {}).items():
                    partition = OrderedDict()
                    for key, entry_data in partition_data.items():
                        entry = WorkingMemoryEntry(
                            key=key,
                            value=entry_data["value"],
                            metadata=entry_data["metadata"],
                            importance=entry_data["importance"],
                            created_at=entry_data["created_at"],
                            last_accessed=entry_data["last_accessed"],
                            access_count=entry_data["access_count"],
                            ttl=entry_data["ttl"],
                            tags=set(entry_data["tags"]),
                        )
                        if not entry.is_expired():
                            partition[key] = entry
                            self._update_tag_index(pk, key, entry.tags, add=True)
                    
                    self._partitions[pk] = partition
                
                self._stats = data.get("stats", self._stats)
            
            logger.info(f"Loaded working memory from {self.persistence_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load working memory: {e}")
            return False


# Convenience class for string values (most common use case)
class StringWorkingMemory(LRUWorkingMemory[str]):
    """Working memory specialized for string values (conversation context)."""
    
    async def append_conversation(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        importance: float = 1.0,
    ) -> str:
        """Append a conversation turn with auto-generated key."""
        key = f"{role}_{int(time.time() * 1000)}"
        meta = metadata or {}
        meta["role"] = role
        meta["turn_index"] = self.get_total_size()
        
        await self.put(key, content, metadata=meta, importance=importance, tags={"conversation"})
        return key
    
    async def get_conversation_window(
        self,
        window_size: int = 20,
        partition_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get recent conversation turns as structured messages."""
        entries = await self.get_sliding_window(
            partition_key=partition_key,
            window_size=window_size,
        )
        
        messages = []
        for entry in entries:
            role = entry.metadata.get("role", "unknown")
            messages.append({"role": role, "content": entry.value})
        
        return messages