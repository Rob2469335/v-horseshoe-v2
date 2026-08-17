from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ModelProfile:
    name: str
    role: str
    max_tokens: int = 4096
    preferred_temp: float = 0.7
    cooldown_seconds: float = 5.0
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


LOCAL_MODEL_SPECS = [
    # Local 4B Tier (MTP 4B, the only served local generation model)
    ModelProfile(
        name="qwen3.5-4b",
        role="general",
        capabilities=["fast", "long_context"],
        metadata={"pp512": 85.0, "tg128": 10.2},
    ),
    ModelProfile(
        name="qwen3.5-4b",
        role="reasoning",
        capabilities=["reasoning", "long_context"],
        metadata={"pp512": 85.0, "tg128": 10.2},
    ),
    ModelProfile(
        name="qwen3.5-4b",
        role="planner",
        capabilities=["reasoning", "long_context"],
        metadata={"pp512": 85.0, "tg128": 10.2},
    ),
    ModelProfile(
        name="qwen3.5-4b",
        role="writer",
        capabilities=["writing", "long_context"],
        metadata={"pp512": 85.0, "tg128": 10.2},
    ),
    ModelProfile(
        name="qwen3.5-4b",
        role="reasoning_long",
        capabilities=["reasoning", "long_context"],
        metadata={"pp512": 85.0, "tg128": 10.2},
    ),
    ModelProfile(
        name="qwen3.5-4b",
        role="deep_coder",
        capabilities=["code", "long_context", "reasoning"],
        metadata={"pp256": 74.9, "tg128": 5.5},
    ),
    ModelProfile(
        name="qwen3.5-4b",
        role="deep_coder_long",
        capabilities=["code", "long_context", "reasoning"],
        metadata={"pp256": 74.9, "tg128": 5.5},
    ),
    ModelProfile(
        name="qwen3.5-4b",
        role="coder_long",
        capabilities=["code", "long_context"],
        metadata={"pp256": 74.9, "tg128": 5.5},
    ),
    # Auxiliary Models (Embeddings, Reranker, Vision)
    ModelProfile(name="Qwen3VL-2B-Instruct", role="vision", capabilities=["vision"]),
    ModelProfile(name="Qwen3-VL-2B", role="vision_alt", capabilities=["vision"]),
    ModelProfile(
        name="gte-modernbert-base", role="embedding", capabilities=["embedding"]
    ),
    ModelProfile(
        name="gte-modernbert-base", role="embedding_alt", capabilities=["embedding"]
    ),
    ModelProfile(
        name="gte-reranker-modernbert-base", role="reranker", capabilities=["rerank"]
    ),
]

CLOUD_MODEL_SPECS = []

ROLE_POOL = {
    "general": ["qwen3.5-4b"],
    "reasoning": ["qwen3.5-4b"],
    "deep_coder": ["qwen3.5-4b"],
    "deep_coder_long": ["qwen3.5-4b"],
    "coder_long": ["qwen3.5-4b"],
    "writer": ["qwen3.5-4b"],
    "vision": ["Qwen3VL-2B-Instruct"],
    "embedding": ["gte-modernbert-base"],
    "reranker": ["gte-reranker-modernbert-base"],
    "planner": ["qwen3.5-4b"],
    "researcher": ["qwen3.5-4b"],
    "fast": ["qwen3.5-4b"],
    "coder": ["qwen3.5-4b"],
    "coder_small": ["qwen3.5-4b"],
}
