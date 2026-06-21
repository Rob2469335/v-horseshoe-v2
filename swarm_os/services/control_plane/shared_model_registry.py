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
    ModelProfile(name="qwen2.5:3b-instruct",              role="fast",            capabilities=["fast"]),
    ModelProfile(name="qwen2.5-coder:3b",                 role="coder_small",     capabilities=["code", "fast"]),
    ModelProfile(name="qwen2.5:7b-instruct",              role="general",         capabilities=["fast", "long_context"]),
    ModelProfile(name="qwen2.5-coder:7b",                 role="coder",           capabilities=["code"]),
    ModelProfile(name="qwen2.5:14b-instruct",             role="reasoning",       capabilities=["reasoning", "long_context"]),
    ModelProfile(name="qwen2.5-coder:14b",                role="deep_coder",      capabilities=["code", "long_context", "reasoning"]),
    ModelProfile(name="qwen3:14b",                        role="planner",         capabilities=["reasoning", "long_context"]),
    ModelProfile(name="qwen3-vl:8b",                      role="vision",          capabilities=["vision"]),
    ModelProfile(name="moondream:latest",                  role="vision_alt",      capabilities=["vision"]),
    ModelProfile(name="qwen3-embedding:8b",               role="embedding",       capabilities=["embedding"]),
    ModelProfile(name="nomic-embed-text:latest",          role="embedding_alt",   capabilities=["embedding"]),
    ModelProfile(name="qllama/bge-reranker-v2-m3:latest", role="reranker",        capabilities=["rerank"]),
    ModelProfile(name="mistral-nemo:12b",                 role="writer",          capabilities=["writing", "long_context"]),
    ModelProfile(name="qwen2.5:14b-instruct-32k",        role="reasoning_long",  capabilities=["reasoning", "long_context"]),
    ModelProfile(name="qwen2.5-coder:14b-32k",           role="deep_coder_long", capabilities=["code", "long_context", "reasoning"]),
    ModelProfile(name="qwen2.5-coder:7b-16k",            role="coder_long",      capabilities=["code", "long_context"]),
]

CLOUD_MODEL_SPECS = [
    # Ollama cloud
    ModelProfile(name="qwen3-coder:480b-cloud",                          role="cloud_heavy",    capabilities=["code", "long_context", "cloud", "reasoning"], metadata={"cloud": True, "provider": "ollama"}),
    # OpenRouter auto-router (picks best free model for task automatically)
    # OpenRouter paid — DeepSeek V4-Flash (best value, ~$0.10/1M tokens)
    ModelProfile(name="deepseek/deepseek-v4-flash",                      role="cloud_paid",     capabilities=["general", "code", "reasoning", "cloud"],      metadata={"cloud": True, "provider": "openrouter"}),
    # OpenRouter free tier
    ModelProfile(name="meta-llama/llama-3.1-8b-instruct:free",           role="cloud_general",  capabilities=["general", "cloud"],                           metadata={"cloud": True, "provider": "openrouter"}),
    ModelProfile(name="mistralai/mistral-7b-instruct:free",              role="cloud_writer",   capabilities=["writing", "cloud"],                           metadata={"cloud": True, "provider": "openrouter"}),
    ModelProfile(name="openrouter/free",                                 role="cloud_free_router", capabilities=["reasoning", "long_context", "cloud"],        metadata={"cloud": True, "provider": "openrouter"}),
    ModelProfile(name="qwen/qwen3-coder:free",                           role="cloud_coder_free", capabilities=["code", "long_context", "cloud", "reasoning"], metadata={"cloud": True, "provider": "openrouter"}),
    ModelProfile(name="deepseek/deepseek-v4-flash:free",                 role="cloud_long_reasoning", capabilities=["reasoning", "long_context", "cloud"], metadata={"cloud": True, "provider": "openrouter"}),
    ModelProfile(name="nvidia/nemotron-3-super-120b-a12b:free",          role="cloud_super_nvidia_free", capabilities=["reasoning", "long_context", "cloud"], metadata={"cloud": True, "provider": "openrouter"}),
    ModelProfile(name="nvidia/nemotron-3-ultra-550b-a55b:free",          role="cloud_ultra_nvidia_free", capabilities=["reasoning", "long_context", "cloud"], metadata={"cloud": True, "provider": "openrouter"}),
    # NVIDIA NIM
    ModelProfile(name="nvidia/llama-3.1-nemotron-nano-8b-v1",            role="cloud_fast",     capabilities=["fast", "general", "cloud"],                   metadata={"cloud": True, "provider": "nvidia"}),
    ModelProfile(name="nvidia/llama-3.3-nemotron-super-49b-v1",          role="cloud_coder",    capabilities=["code", "reasoning", "cloud"],                 metadata={"cloud": True, "provider": "nvidia"}),
    ModelProfile(name="meta/llama-3.3-70b-instruct",                     role="cloud_pro_nvidia",capabilities=["reasoning", "long_context", "cloud"],         metadata={"cloud": True, "provider": "nvidia"}),
    ModelProfile(name="meta/llama-3.1-405b-instruct",                    role="cloud_heavy_nvidia",capabilities=["reasoning", "long_context", "cloud"],       metadata={"cloud": True, "provider": "nvidia"}),
]

# ORDER: single fast local model per role, planner has full fallback chain
ROLE_POOL = {
    "fast":             ["qwen2.5:3b-instruct"],
    "coder_small":      ["qwen2.5-coder:3b"],
    "general":          ["qwen2.5:7b-instruct"],
    "coder":            ["qwen2.5-coder:7b"],
    "reasoning":        ["qwen2.5:14b-instruct"],
    "deep_coder":       ["qwen2.5-coder:14b"],
    "deep_coder_long":  ["qwen2.5-coder:14b"],
    "coder_long":       ["qwen2.5-coder:7b-16k"],
    "writer":           ["mistral-nemo:12b"],
    "vision":           ["qwen3-vl:8b"],
    "embedding":        ["nomic-embed-text:latest"],
    "reranker":         ["qllama/bge-reranker-v2-m3:latest"],
    "cloud_heavy":      ["qwen3-coder:480b-cloud"],
    "cloud_general":    ["qwen2.5:7b-instruct"],
    "cloud_writer":     ["mistral-nemo:12b"],
    "cloud_reasoning":  ["qwen2.5:14b-instruct"],
    "cloud_coder":      ["qwen2.5-coder:14b"],
    "cloud_fast":       ["qwen2.5:7b-instruct"],
    "cloud_pro":        ["qwen3-coder:480b-cloud"],
    "planner":          ["qwen3-coder:480b-cloud", "deepseek/deepseek-chat-v3-5:free", "nvidia/nemotron-3-super-120b-a12b:free", "nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter/free", "qwen3:14b", "deepseek/deepseek-v4-flash"],
}




