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
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="fast",            capabilities=["fast"]),
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="coder_small",     capabilities=["code", "fast"]),
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="general",         capabilities=["fast", "long_context"]),
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="coder",           capabilities=["code"]),
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="reasoning",       capabilities=["reasoning", "long_context"]),
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="deep_coder",      capabilities=["code", "long_context", "reasoning"]),
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="planner",         capabilities=["reasoning", "long_context"]),
    ModelProfile(name="qwen3-vl:8b",                                                role="vision",          capabilities=["vision"]),
    ModelProfile(name="moondream:latest",                                           role="vision_alt",      capabilities=["vision"]),
    ModelProfile(name="qwen3-embedding:8b",                                         role="embedding",       capabilities=["embedding"]),
    ModelProfile(name="nomic-embed-text:latest",                                    role="embedding_alt",   capabilities=["embedding"]),
    ModelProfile(name="qllama/bge-reranker-v2-m3:latest",                           role="reranker",        capabilities=["rerank"]),
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="writer",          capabilities=["writing", "long_context"]),
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="reasoning_long",  capabilities=["reasoning", "long_context"]),
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="deep_coder_long", capabilities=["code", "long_context", "reasoning"]),
    ModelProfile(name="danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS", role="coder_long",      capabilities=["code", "long_context"]),
]

CLOUD_MODEL_SPECS = []

ROLE_POOL = {
    "fast":             ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
    "coder_small":      ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
    "general":          ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
    "coder":            ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
    "reasoning":        ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
    "deep_coder":       ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
    "deep_coder_long":  ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
    "coder_long":       ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
    "writer":           ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
    "vision":           ["qwen3-vl:8b"],
    "embedding":        ["qwen3-embedding:8b"],
    "reranker":         ["qllama/bge-reranker-v2-m3:latest"],
    "planner":          ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
    "researcher":       ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"],
}

