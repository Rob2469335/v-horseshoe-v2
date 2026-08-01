"""
Unit tests for Hybrid Memory Layer.

Tests cover:
- Vector store recall accuracy and latency
- Episodic memory temporal queries
- Working memory LRU eviction
- Hybrid memory integration
"""

import asyncio
import time
import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agent_memory.vector_store import VectorStore, SearchResult
from agent_memory.episodic_store import (
    EpisodicStore, Episode, EpisodeType, EpisodicQuery
)
from agent_memory.working_memory import LRUWorkingMemory, WorkingMemoryEntry, StringWorkingMemory
from agent_memory.hybrid_memory import (
    HybridMemory, MemoryConfig, MemoryContext, EpisodeType as HybridEpisodeType
)


# Fixtures

@pytest.fixture
def vector_store():
    """Create a vector store for testing."""
    return VectorStore(
        dimension=384,
        similarity_threshold=0.7,
        cache_size=1000,
    )


@pytest.fixture
def episodic_store():
    """Create an episodic store for testing."""
    return EpisodicStore(
        max_episodes=1000,
        auto_persist=False,
    )


@pytest.fixture
def working_memory():
    """Create a working memory for testing."""
    return LRUWorkingMemory[str](
        max_size=100,
        default_ttl=3600.0,
        partition_by="agent_id",
    )


@pytest.fixture
def string_working_memory():
    """Create a string working memory for testing."""
    return StringWorkingMemory(
        max_size=100,
        default_ttl=3600.0,
    )


@pytest.fixture
def hybrid_memory():
    """Create a hybrid memory for testing."""
    config = MemoryConfig(
        vector_dimension=384,
        episodic_max_episodes=1000,
        working_max_size=100,
    )
    memory = HybridMemory(config=config)
    # Mock embed function
    memory.set_embed_fn(lambda text: np.random.randn(384).astype(np.float32))
    return memory


@pytest.fixture
def sample_embeddings():
    """Generate sample embeddings for testing."""
    np.random.seed(42)
    return {
        "python": np.random.randn(384).astype(np.float32),
        "javascript": np.random.randn(384).astype(np.float32),
        "async": np.random.randn(384).astype(np.float32),
        "error": np.random.randn(384).astype(np.float32),
    }


# Vector Store Tests

class TestVectorStore:
    """Tests for VectorStore."""
    
    @pytest.mark.asyncio
    async def test_add_and_search(self, vector_store, sample_embeddings):
        """Test adding vectors and searching."""
        # Add vectors - use same embedding for search to ensure match
        search_embedding = sample_embeddings["python"]
        await vector_store.add("Python async patterns", search_embedding)
        await vector_store.add("JavaScript async/await", sample_embeddings["javascript"])
        
        # Search with the same embedding
        results = await vector_store.search(search_embedding, top_k=2)
        
        assert len(results) >= 1
        assert all(r.score >= 0.7 for r in results)
    
    @pytest.mark.asyncio
    async def test_search_latency(self, vector_store, sample_embeddings):
        """Test search latency < 50ms."""
        # Add many vectors
        for i in range(100):
            vec = np.random.randn(384).astype(np.float32)
            await vector_store.add(f"doc_{i}", vec)
        
        query_vec = np.random.randn(384).astype(np.float32)
        
        start = time.perf_counter()
        results = await vector_store.search(query_vec, top_k=10)
        latency_ms = (time.perf_counter() - start) * 1000
        
        assert latency_ms < 50, f"Search latency {latency_ms:.2f}ms exceeds 50ms threshold"
    
    @pytest.mark.asyncio
    async def test_batch_add(self, vector_store):
        """Test batch addition."""
        texts = [f"text_{i}" for i in range(10)]
        vectors = np.random.randn(10, 384).astype(np.float32)
        
        ids = await vector_store.add_batch(texts, vectors)
        
        assert len(ids) == 10
        assert vector_store.get_stats()["total_entries"] == 10
    
    @pytest.mark.asyncio
    async def test_cache_hit_rate(self, vector_store):
        """Test embedding cache functionality."""
        text = "test query"
        vector = np.random.randn(384).astype(np.float32)
        
        # First search (cache miss)
        async def embed_fn(t):
            return vector
        
        await vector_store.search_by_text(text, embed_fn, top_k=1)
        stats1 = vector_store.get_stats()
        
        # Second search (cache hit)
        await vector_store.search_by_text(text, embed_fn, top_k=1)
        stats2 = vector_store.get_stats()
        
        assert stats2["cache_hit_rate"] > stats1["cache_hit_rate"]


# Episodic Store Tests

class TestEpisodicStore:
    """Tests for EpisodicStore."""
    
    @pytest.mark.asyncio
    async def test_add_episode(self, episodic_store):
        """Test adding episodes."""
        episode = await episodic_store.add(
            episode_type=EpisodeType.USER_MESSAGE,
            agent_id="agent_1",
            content="Hello world",
            metadata={"topic": "greeting"},
            importance=0.8,
            tags={"test", "greeting"},
        )
        
        assert episode.id is not None
        assert episode.content == "Hello world"
        assert episode.importance == 0.8
        assert "greeting" in episode.tags
    
    @pytest.mark.asyncio
    async def test_query_by_agent(self, episodic_store):
        """Test querying episodes by agent."""
        await episodic_store.add(
            episode_type=EpisodeType.AGENT_MESSAGE,
            agent_id="agent_1",
            content="Message 1",
        )
        await episodic_store.add(
            episode_type=EpisodeType.AGENT_MESSAGE,
            agent_id="agent_2",
            content="Message 2",
        )
        
        episodes = await episodic_store.query(EpisodicQuery(
            agent_id="agent_1",
            limit=10,
        ))
        
        assert len(episodes) == 1
        assert episodes[0].agent_id == "agent_1"
    
    @pytest.mark.asyncio
    async def test_query_by_type(self, episodic_store):
        """Test querying by episode type."""
        await episodic_store.add(
            episode_type=EpisodeType.TASK_START,
            agent_id="agent_1",
            content="Task started",
        )
        await episodic_store.add(
            episode_type=EpisodeType.TASK_COMPLETE,
            agent_id="agent_1",
            content="Task completed",
        )
        
        episodes = await episodic_store.query(EpisodicQuery(
            agent_id="agent_1",
            episode_types=[EpisodeType.TASK_COMPLETE],
        ))
        
        assert len(episodes) == 1
        assert episodes[0].episode_type == EpisodeType.TASK_COMPLETE
    
    @pytest.mark.asyncio
    async def test_conversation_thread(self, episodic_store):
        """Test conversation thread retrieval."""
        root = await episodic_store.add(
            episode_type=EpisodeType.USER_MESSAGE,
            agent_id="user",
            content="Root message",
        )
        
        child = await episodic_store.add(
            episode_type=EpisodeType.AGENT_MESSAGE,
            agent_id="agent_1",
            content="Reply",
            parent_id=root.id,
        )
        
        thread = await episodic_store.get_conversation_thread(root.id)
        
        assert len(thread) == 2
        assert thread[0].id == root.id
        assert thread[1].id == child.id
    
    @pytest.mark.asyncio
    async def test_time_range_query(self, episodic_store):
        """Test querying by time range."""
        start = time.time()
        
        await episodic_store.add(
            episode_type=EpisodeType.SYSTEM_EVENT,
            agent_id="system",
            content="Event 1",
        )
        
        await asyncio.sleep(0.01)
        
        await episodic_store.add(
            episode_type=EpisodeType.SYSTEM_EVENT,
            agent_id="system",
            content="Event 2",
        )
        
        end = time.time()
        
        episodes = await episodic_store.query(EpisodicQuery(
            agent_id="system",
            time_range=(start, end),
        ))
        
        assert len(episodes) >= 1


# Working Memory Tests

class TestWorkingMemory:
    """Tests for LRUWorkingMemory."""
    
    @pytest.mark.asyncio
    async def test_put_and_get(self, working_memory):
        """Test basic put/get operations."""
        await working_memory.put("key1", "value1")
        
        value = await working_memory.get("key1")
        
        assert value == "value1"
    
    @pytest.mark.asyncio
    async def test_lru_eviction(self, working_memory):
        """Test LRU eviction when capacity exceeded."""
        working_memory.max_size = 3
        
        await working_memory.put("key1", "value1")
        await asyncio.sleep(0.02)
        await working_memory.put("key2", "value2")
        await asyncio.sleep(0.02)
        await working_memory.put("key3", "value3")
        
        # Access key1 to make it recently used
        await asyncio.sleep(0.02)
        await working_memory.get("key1")
        
        # Add key4, should evict key2 (least recently used)
        await asyncio.sleep(0.02)
        await working_memory.put("key4", "value4")
        
        assert await working_memory.get("key1") == "value1"
        assert await working_memory.get("key2") is None  # Evicted
        assert await working_memory.get("key3") == "value3"
        assert await working_memory.get("key4") == "value4"
    
    @pytest.mark.asyncio
    async def test_importance_weighted_eviction(self, working_memory):
        """Test importance-weighted eviction."""
        working_memory.max_size = 2
        
        await working_memory.put("low", "value1", importance=0.1)
        await working_memory.put("high", "value2", importance=0.9)
        
        # Add third item, should evict low importance
        await working_memory.put("medium", "value3", importance=0.5)
        
        assert await working_memory.get("high") == "value2"
        assert await working_memory.get("medium") == "value3"
        assert await working_memory.get("low") is None  # Evicted
    
    @pytest.mark.asyncio
    async def test_ttl_expiration(self, working_memory):
        """Test TTL-based expiration."""
        await working_memory.put("temp", "value", ttl=0.01)  # 10ms TTL
        
        assert await working_memory.get("temp") == "value"
        
        await asyncio.sleep(0.02)
        
        assert await working_memory.get("temp") is None
    
    @pytest.mark.asyncio
    async def test_partitioning(self, working_memory):
        """Test partition by agent_id."""
        await working_memory.put(
            "key1", "value1",
            metadata={"agent_id": "agent_1"}
        )
        await working_memory.put(
            "key1", "value2",
            metadata={"agent_id": "agent_2"}
        )
        
        # Different partitions
        assert await working_memory.get("key1", metadata={"agent_id": "agent_1"}) == "value1"
        assert await working_memory.get("key1", metadata={"agent_id": "agent_2"}) == "value2"
    
    @pytest.mark.asyncio
    async def test_tag_indexing(self, working_memory):
        """Test tag-based retrieval."""
        await working_memory.put("key1", "value1", tags={"important", "task"})
        await working_memory.put("key2", "value2", tags={"task"})
        await working_memory.put("key3", "value3", tags={"other"})
        
        # Get by tags (AND)
        results = await working_memory.get_by_tags({"important", "task"}, match_all=True)
        assert len(results) == 1
        assert results[0].key == "key1"
        
        # Get by tags (OR)
        results = await working_memory.get_by_tags({"important", "other"}, match_all=False)
        assert len(results) == 2
    
    @pytest.mark.asyncio
    async def test_sliding_window(self, string_working_memory):
        """Test sliding window for conversation context."""
        for i in range(10):
            await string_working_memory.append_conversation(
                "user" if i % 2 == 0 else "assistant", 
                f"Message {i}",
                metadata={"turn_index": i}
            )
            await asyncio.sleep(0.001)  # Ensure unique timestamps
        
        window = await string_working_memory.get_conversation_window(window_size=4)
        
        assert len(window) == 4
        assert window[-1]["content"] == "Message 9"


# Hybrid Memory Tests

class TestHybridMemory:
    """Tests for HybridMemory integration."""
    
    @pytest.mark.asyncio
    async def test_remember_and_recall(self, hybrid_memory):
        """Test remembering and recalling."""
        episode = await hybrid_memory.remember(
            agent_id="agent_1",
            content="User asked about Python async",
            episode_type=HybridEpisodeType.USER_MESSAGE,
            metadata={"topic": "python"},
            importance=0.8,
        )
        
        assert episode.id is not None
        
        # Recall
        context = await hybrid_memory.recall(
            agent_id="agent_1",
            query="Python async",
        )
        
        assert len(context.episodes) >= 1
        assert context.agent_id == "agent_1"
    
    @pytest.mark.asyncio
    async def test_conversation_context(self, hybrid_memory):
        """Test getting conversation context for LLM."""
        # Add conversation turns
        await hybrid_memory.remember(
            agent_id="agent_1",
            content="Hello",
            episode_type=HybridEpisodeType.USER_MESSAGE,
        )
        await hybrid_memory.remember(
            agent_id="agent_1",
            content="Hi there! How can I help?",
            episode_type=HybridEpisodeType.AGENT_MESSAGE,
        )
        
        context = await hybrid_memory.get_conversation_context("agent_1", window_size=10)
        
        assert len(context) == 2
        assert context[0]["role"] == "user"
        assert context[1]["role"] == "assistant"
    
    @pytest.mark.asyncio
    async def test_agent_timeline(self, hybrid_memory):
        """Test getting agent timeline."""
        for i in range(5):
            await hybrid_memory.remember(
                agent_id="agent_1",
                content=f"Action {i}",
                episode_type=HybridEpisodeType.TASK_START if i % 2 == 0 else HybridEpisodeType.TASK_COMPLETE,
            )
        
        timeline = await hybrid_memory.get_agent_timeline("agent_1", limit=10)
        
        assert len(timeline) == 5
    
    @pytest.mark.asyncio
    async def test_combined_context(self, hybrid_memory):
        """Test combined context generation."""
        # Add various memories
        await hybrid_memory.remember(
            agent_id="agent_1",
            content="Python async best practices",
            episode_type=HybridEpisodeType.AGENT_MESSAGE,
            importance=0.9,
        )
        
        context = await hybrid_memory.recall(
            agent_id="agent_1",
            query="async patterns",
            include_working=True,
            include_episodic=True,
            include_semantic=True,
        )
        
        combined = context.get_combined_context(max_chars=5000)
        
        assert "Active Context" in combined or "Relevant History" in combined
    
    @pytest.mark.asyncio
    async def test_stats(self, hybrid_memory):
        """Test statistics collection."""
        await hybrid_memory.remember(
            agent_id="agent_1",
            content="Test",
            episode_type=HybridEpisodeType.SYSTEM_EVENT,
        )
        
        stats = hybrid_memory.get_stats()
        
        assert "hybrid" in stats
        assert "vector_store" in stats
        assert "episodic_store" in stats
        assert "working_memory" in stats


# Integration/Performance Tests

class TestMemoryPerformance:
    """Performance benchmarks for memory system."""
    
    @pytest.mark.asyncio
    async def test_recall_latency_under_50ms(self, hybrid_memory):
        """Test that recall latency is under 50ms."""
        # Pre-populate with data
        for i in range(100):
            await hybrid_memory.remember(
                agent_id=f"agent_{i % 5}",
                content=f"Memory content {i}",
                episode_type=HybridEpisodeType.SYSTEM_EVENT,
            )
        
        # Measure recall latency
        start = time.perf_counter()
        context = await hybrid_memory.recall(
            agent_id="agent_1",
            query="test query",
        )
        latency_ms = (time.perf_counter() - start) * 1000
        
        assert latency_ms < 50, f"Recall latency {latency_ms:.2f}ms exceeds 50ms"
    
    @pytest.mark.asyncio
    async def test_concurrent_operations(self, hybrid_memory):
        """Test concurrent memory operations."""
        async def add_memories(agent_id: str, count: int):
            for i in range(count):
                await hybrid_memory.remember(
                    agent_id=agent_id,
                    content=f"Memory {i} from {agent_id}",
                    episode_type=HybridEpisodeType.SYSTEM_EVENT,
                )
        
        async def recall_memories(agent_id: str, count: int):
            for _ in range(count):
                await hybrid_memory.recall(agent_id=agent_id, query="test")
        
        # Run concurrent operations
        await asyncio.gather(
            add_memories("agent_1", 50),
            add_memories("agent_2", 50),
            recall_memories("agent_1", 20),
            recall_memories("agent_2", 20),
        )
        
        stats = hybrid_memory.get_stats()
        assert stats["hybrid"]["total_remembers"] >= 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])