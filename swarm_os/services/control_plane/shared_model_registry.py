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
    ModelProfile(name="phi4-mini:latest",                 role="fast",            capabilities=["fast"]),
    ModelProfile(name="qwen2.5-coder:3b",                 role="coder_small",     capabilities=["code", "fast"]),
    ModelProfile(name="qwen3.5:9b",                       role="general",         capabilities=["fast", "long_context"]),
    ModelProfile(name="qwen2.5-coder:7b",                 role="coder",           capabilities=["code"]),
    ModelProfile(name="qwen3.5:9b",                       role="reasoning",       capabilities=["reasoning", "long_context"]),
    ModelProfile(name="qwen2.5-coder:7b",                 role="deep_coder",      capabilities=["code", "long_context", "reasoning"]),
    ModelProfile(name="qwen3.5:9b",                       role="planner",         capabilities=["reasoning", "long_context"]),
    ModelProfile(name="qwen3-vl:8b",                      role="vision",          capabilities=["vision"]),
    ModelProfile(name="moondream:latest",                 role="vision_alt",      capabilities=["vision"]),
    ModelProfile(name="qwen3-embedding:8b",               role="embedding",       capabilities=["embedding"]),
    ModelProfile(name="nomic-embed-text:latest",          role="embedding_alt",   capabilities=["embedding"]),
    ModelProfile(name="qllama/bge-reranker-v2-m3:latest", role="reranker",        capabilities=["rerank"]),
    ModelProfile(name="ministral-3:8b",                   role="writer",          capabilities=["writing", "long_context"]),
    ModelProfile(name="qwen3.5:9b",                       role="reasoning_long",  capabilities=["reasoning", "long_context"]),
    ModelProfile(name="qwen2.5-coder:7b",                 role="deep_coder_long", capabilities=["code", "long_context", "reasoning"]),
    ModelProfile(name="qwen2.5-coder:7b",                 role="coder_long",      capabilities=["code", "long_context"]),
]

CLOUD_MODEL_SPECS = []

ROLE_POOL = {
    "fast":             ["phi4-mini:latest"],
    "coder_small":      ["qwen2.5-coder:3b"],
    "general":          ["qwen3.5:9b"],
    "coder":            ["qwen2.5-coder:7b"],
    "reasoning":        ["qwen3.5:9b"],
    "deep_coder":       ["qwen2.5-coder:7b"],
    "deep_coder_long":  ["qwen2.5-coder:7b"],
    "coder_long":       ["qwen2.5-coder:7b"],
    "writer":           ["ministral-3:8b"],
    "vision":           ["qwen3-vl:8b"],
    "embedding":        ["qwen3-embedding:8b"],
    "reranker":         ["qllama/bge-reranker-v2-m3:latest"],
    "planner":          ["qwen3.5:9b"],
    "researcher":       ["qwen3.5:9b"],
}
