"""
Agent Memory System - Hybrid Memory Layer

Combines:
- Vector Memory (Semantic Embeddings) for semantic recall
- Episodic Memory (Timeline Store) for temporal/event recall
- Working Memory (LRU Cache) for active context

Inspired by LangGraph's memory patterns and AutoGen's conversation history structures.
"""

from .vector_store import VectorStore, VectorEntry, SearchResult
from .episodic_store import EpisodicStore, Episode, EpisodeType, EpisodicQuery
from .working_memory import LRUWorkingMemory as WorkingMemory, WorkingMemoryEntry
from .hybrid_memory import HybridMemory, MemoryConfig, MemoryContext

__all__ = [
    "VectorStore",
    "VectorEntry", 
    "SearchResult",
    "SimilarityMetric",
    "EpisodicStore",
    "Episode",
    "EpisodeType",
    "EpisodicQuery",
    "WorkingMemory",
    "WorkingMemoryEntry",
    "HybridMemory",
    "MemoryConfig",
    "MemoryContext",
]

__version__ = "1.0.0"