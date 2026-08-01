"""
Vector Embedding Store for Semantic Recall.

Provides high-performance semantic search with configurable similarity thresholds.
Target: latency < 50ms, retrieval F1 >= 0.85 on benchmark corpus.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class SimilarityMetric:
    COSINE = "cosine"
    DOT_PRODUCT = "dot_product"
    EUCLIDEAN = "euclidean"


@dataclass
class VectorEntry:
    """Single vector embedding entry with metadata."""
    id: str
    vector: np.ndarray
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)


@dataclass
class SearchResult:
    """Result from vector similarity search."""
    entry: VectorEntry
    score: float
    metadata: Dict[str, Any]


class VectorStore:
    """
    High-performance vector embedding store with semantic recall capabilities.
    
    Features:
    - Cosine similarity search with configurable thresholds
    - LRU cache for frequently accessed embeddings
    - Batch insertion for bulk operations
    - Configurable similarity metrics (cosine, euclidean, dot_product)
    - Latency target: < 50ms for top-k retrieval
    """
    
    def __init__(
        self,
        dimension: int = 768,
        similarity_threshold: float = 0.75,
        cache_size: int = 10000,
        similarity_metric: str = "cosine",
        enable_cache: bool = True,
    ):
        """
        Initialize vector embedding store.
        
        Args:
            dimension: Embedding vector dimension
            similarity_threshold: Minimum similarity score for results (0-1)
            cache_size: Maximum entries in LRU cache
            similarity_metric: 'cosine', 'euclidean', or 'dot_product'
            enable_cache: Enable LRU embedding cache
        """
        self.dimension = dimension
        self.similarity_threshold = similarity_threshold
        self.similarity_metric = similarity_metric
        self.enable_cache = enable_cache
        
        # Main storage: id -> VectorEntry
        self._store: Dict[str, VectorEntry] = {}
        
        # LRU cache for embeddings: text_hash -> (vector, timestamp)
        self._embedding_cache: OrderedDict[str, Tuple[np.ndarray, float]] = OrderedDict()
        self._cache_size = cache_size
        
        # Vector index for fast search (simple list for now, can be replaced with FAISS/Annoy)
        self._vectors: List[np.ndarray] = []
        self._ids: List[str] = []
        
        # Stats
        self._stats = {
            "total_queries": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "total_latency_ms": 0.0,
        }
        
        # Lock for thread safety
        self._lock = asyncio.Lock()
    
    def _compute_hash(self, text: str) -> str:
        """Compute deterministic hash for text."""
        return hashlib.sha256(text.encode()).hexdigest()[:16]
    
    def _get_cached_embedding(self, text: str) -> Optional[np.ndarray]:
        """Get embedding from cache if available."""
        if not self.enable_cache:
            return None
            
        text_hash = self._compute_hash(text)
        if text_hash in self._embedding_cache:
            vector, _ = self._embedding_cache.pop(text_hash)
            self._embedding_cache[text_hash] = (vector, time.time())
            self._stats["cache_hits"] += 1
            return vector
        
        self._stats["cache_misses"] += 1
        return None
    
    def _cache_embedding(self, text: str, vector: np.ndarray) -> None:
        """Cache embedding with LRU eviction."""
        if not self.enable_cache:
            return
            
        text_hash = self._compute_hash(text)
        
        # Evict if at capacity
        if len(self._embedding_cache) >= self._cache_size:
            self._embedding_cache.popitem(last=False)
        
        self._embedding_cache[text_hash] = (vector, time.time())
    
    def _compute_similarity(self, query_vec: np.ndarray, doc_vec: np.ndarray) -> float:
        """Compute similarity between two vectors based on configured metric."""
        if self.similarity_metric == "cosine":
            # Cosine similarity
            norm_q = np.linalg.norm(query_vec)
            norm_d = np.linalg.norm(doc_vec)
            if norm_q == 0 or norm_d == 0:
                return 0.0
            return float(np.dot(query_vec, doc_vec) / (norm_q * norm_d))
        elif self.similarity_metric == "dot_product":
            return float(np.dot(query_vec, doc_vec))
        elif self.similarity_metric == "euclidean":
            # Convert euclidean distance to similarity (0-1)
            dist = np.linalg.norm(query_vec - doc_vec)
            return float(1.0 / (1.0 + dist))
        else:
            raise ValueError(f"Unknown similarity metric: {self.similarity_metric}")
    
    async def add(
        self,
        text: str,
        vector: np.ndarray,
        metadata: Optional[Dict[str, Any]] = None,
        entry_id: Optional[str] = None,
    ) -> str:
        """
        Add a text-vector pair to the store.
        
        Args:
            text: Original text content
            vector: Embedding vector (must match dimension)
            metadata: Optional metadata dictionary
            entry_id: Optional custom ID (auto-generated if not provided)
            
        Returns:
            Entry ID
        """
        async with self._lock:
            # Validate vector dimension
            if vector.shape[0] != self.dimension:
                raise ValueError(f"Vector dimension {vector.shape[0]} != expected {self.dimension}")
            
            # Generate ID if not provided
            if entry_id is None:
                entry_id = self._compute_hash(text + str(time.time()))
            
            # Create entry
            entry = VectorEntry(
                id=entry_id,
                vector=vector.copy(),
                text=text,
                metadata=metadata or {},
            )
            
            # Store
            self._store[entry_id] = entry
            self._vectors.append(entry.vector)
            self._ids.append(entry_id)
            
            # Cache embedding
            self._cache_embedding(text, vector)
            
            logger.debug(f"Added vector entry {entry_id} (dim={self.dimension})")
            return entry_id
    
    async def add_batch(
        self,
        texts: List[str],
        vectors: np.ndarray,
        metadata_list: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        """Add multiple entries in batch."""
        if vectors.shape[0] != len(texts):
            raise ValueError("Number of vectors must match number of texts")
        
        if metadata_list is None:
            metadata_list = [{}] * len(texts)
        
        ids = []
        async with self._lock:
            for i, (text, vector) in enumerate(zip(texts, vectors)):
                entry_id = self._compute_hash(text + str(time.time()) + str(i))
                entry = VectorEntry(
                    id=entry_id,
                    vector=vector.copy(),
                    text=text,
                    metadata=metadata_list[i],
                )
                self._store[entry_id] = entry
                self._vectors.append(entry.vector)
                self._ids.append(entry_id)
                self._cache_embedding(text, vector)
                ids.append(entry_id)
        
        logger.debug(f"Batch added {len(ids)} vector entries")
        return ids
    
    async def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 10,
        threshold: Optional[float] = None,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """
        Search for similar vectors.
        
        Args:
            query_vector: Query embedding vector
            top_k: Maximum number of results
            threshold: Override similarity threshold
            filter_metadata: Optional metadata filters (exact match)
            
        Returns:
            List of SearchResult sorted by similarity (highest first)
        """
        start_time = time.perf_counter()
        
        async with self._lock:
            if not self._vectors:
                return []
            
            threshold = threshold if threshold is not None else self.similarity_threshold
            
            # Convert to numpy array for batch computation
            vectors_array = np.array(self._vectors)
            
            # Compute similarities in batch
            if self.similarity_metric == "cosine":
                # Normalize vectors
                query_norm = np.linalg.norm(query_vector)
                if query_norm == 0:
                    return []
                
                doc_norms = np.linalg.norm(vectors_array, axis=1)
                valid = doc_norms > 0
                
                if not np.any(valid):
                    return []
                
                similarities = np.zeros(len(vectors_array))
                similarities[valid] = np.dot(vectors_array[valid], query_vector) / (doc_norms[valid] * query_norm)
            elif self.similarity_metric == "dot_product":
                similarities = np.dot(vectors_array, query_vector)
            else:  # euclidean
                distances = np.linalg.norm(vectors_array - query_vector, axis=1)
                similarities = 1.0 / (1.0 + distances)
            
            # Apply metadata filter if provided
            if filter_metadata:
                mask = np.ones(len(self._ids), dtype=bool)
                for i, entry_id in enumerate(self._ids):
                    entry = self._store[entry_id]
                    for key, value in filter_metadata.items():
                        if entry.metadata.get(key) != value:
                            mask[i] = False
                            break
                similarities = similarities * mask
            
            # Get top-k indices above threshold
            valid_indices = np.where(similarities >= threshold)[0]
            if len(valid_indices) == 0:
                return []
            
            # Sort by similarity descending
            sorted_indices = valid_indices[np.argsort(similarities[valid_indices])[::-1]]
            top_indices = sorted_indices[:top_k]
            
            # Build results
            results = []
            for idx in top_indices:
                entry_id = self._ids[idx]
                entry = self._store[entry_id]
                entry.access_count += 1
                entry.last_accessed = time.time()
                
                results.append(SearchResult(
                    entry=entry,
                    score=float(similarities[idx]),
                    metadata=entry.metadata.copy(),
                ))
            
            # Update stats
            latency_ms = (time.perf_counter() - start_time) * 1000
            self._stats["total_queries"] += 1
            self._stats["total_latency_ms"] += latency_ms
            
            logger.debug(f"Vector search: {len(results)} results in {latency_ms:.2f}ms")
            return results
    
    async def search_by_text(
        self,
        query_text: str,
        embed_fn,
        top_k: int = 10,
        threshold: Optional[float] = None,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """
        Search by text using provided embedding function.
        
        Args:
            query_text: Query text
            embed_fn: Function(text) -> np.ndarray (sync or async)
            top_k: Maximum results
            threshold: Similarity threshold override
            filter_metadata: Metadata filters
        """
        # Check cache first
        cached = self._get_cached_embedding(query_text)
        if cached is not None:
            query_vector = cached
        else:
            # Handle both sync and async embedding functions
            import inspect
            if inspect.iscoroutinefunction(embed_fn):
                query_vector = await embed_fn(query_text)
            else:
                query_vector = embed_fn(query_text)
            self._cache_embedding(query_text, query_vector)
        
        return await self.search(query_vector, top_k, threshold, filter_metadata)
    
    async def get(self, entry_id: str) -> Optional[VectorEntry]:
        """Get entry by ID."""
        async with self._lock:
            entry = self._store.get(entry_id)
            if entry:
                entry.access_count += 1
                entry.last_accessed = time.time()
            return entry
    
    async def delete(self, entry_id: str) -> bool:
        """Delete entry by ID."""
        async with self._lock:
            if entry_id in self._store:
                del self._store[entry_id]
                # Rebuild vectors list (could be optimized with index)
                idx = self._ids.index(entry_id)
                self._ids.pop(idx)
                self._vectors.pop(idx)
                return True
            return False
    
    async def update_metadata(self, entry_id: str, metadata: Dict[str, Any]) -> bool:
        """Update entry metadata."""
        async with self._lock:
            if entry_id in self._store:
                self._store[entry_id].metadata.update(metadata)
                return True
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get store statistics."""
        avg_latency = (
            self._stats["total_latency_ms"] / self._stats["total_queries"]
            if self._stats["total_queries"] > 0 else 0
        )
        cache_hit_rate = (
            self._stats["cache_hits"] / (self._stats["cache_hits"] + self._stats["cache_misses"])
            if (self._stats["cache_hits"] + self._stats["cache_misses"]) > 0 else 0
        )
        
        return {
            "total_entries": len(self._store),
            "dimension": self.dimension,
            "similarity_threshold": self.similarity_threshold,
            "similarity_metric": self.similarity_metric,
            "cache_size": len(self._embedding_cache),
            "cache_max_size": self._cache_size,
            "cache_hit_rate": cache_hit_rate,
            "avg_query_latency_ms": avg_latency,
            "total_queries": self._stats["total_queries"],
        }
    
    async def clear(self) -> None:
        """Clear all entries."""
        async with self._lock:
            self._store.clear()
            self._vectors.clear()
            self._ids.clear()
            self._embedding_cache.clear()
            self._stats = {
                "total_queries": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "total_latency_ms": 0.0,
            }