path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\control_plane\shared_model_registry.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

import re
new_pool = """# ORDER: Local Ollama -> OpenRouter free fusion -> NVIDIA free -> OpenRouter paid
ROLE_POOL = {
    "fast":             ["qwen2.5:3b-instruct"],
    "coder_small":      ["qwen2.5-coder:3b"],
    "general":          ["qwen2.5:7b-instruct",       "openrouter/fusion",               "nvidia/llama-3.1-nemotron-nano-8b-v1",    "deepseek/deepseek-v4-flash"],
    "coder":            ["qwen2.5-coder:7b",           "qwen3-coder:480b-cloud",          "openrouter/fusion",                       "nvidia/llama-3.1-nemotron-nano-8b-v1",   "deepseek/deepseek-v4-flash"],
    "reasoning":        ["qwen2.5:14b-instruct",       "qwen3:14b",                       "openrouter/fusion",                       "nvidia/llama-3.1-nemotron-nano-8b-v1",   "deepseek/deepseek-v4-flash"],
    "deep_coder":       ["qwen3-coder:480b-cloud",     "openrouter/fusion",               "nvidia/llama-3.1-nemotron-nano-8b-v1",    "deepseek/deepseek-v4-flash"],
    "planner":          ["qwen3:14b",                  "qwen3-coder:480b-cloud",          "openrouter/fusion",                       "nvidia/llama-3.1-nemotron-nano-8b-v1",   "deepseek/deepseek-v4-flash"],
    "writer":           ["mistral-nemo:12b",            "openrouter/fusion",               "deepseek/deepseek-v4-flash"],
    "vision":           ["qwen3-vl:8b",                "moondream:latest"],
    "embedding":        ["qwen3-embedding:8b",          "nomic-embed-text:latest"],
    "reranker":         ["qllama/bge-reranker-v2-m3:latest"],
    "cloud_heavy":      ["qwen3-coder:480b-cloud",     "openrouter/fusion",               "deepseek/deepseek-v4-flash"],
    "cloud_general":    ["openrouter/fusion",           "nvidia/llama-3.1-nemotron-nano-8b-v1", "deepseek/deepseek-v4-flash"],
    "cloud_writer":     ["openrouter/fusion",           "deepseek/deepseek-v4-flash"],
    "cloud_reasoning":  ["qwen3-coder:480b-cloud",     "openrouter/fusion",               "nvidia/llama-3.1-nemotron-nano-8b-v1",   "deepseek/deepseek-v4-flash"],
    "cloud_coder":      ["qwen3-coder:480b-cloud",     "openrouter/fusion",               "nvidia/llama-3.1-nemotron-nano-8b-v1",   "deepseek/deepseek-v4-flash"],
    "cloud_fast":       ["openrouter/fusion",           "nvidia/llama-3.1-nemotron-nano-8b-v1", "deepseek/deepseek-v4-flash"],
    "cloud_pro":        ["qwen3-coder:480b-cloud",     "openrouter/fusion",               "deepseek/deepseek-v4-flash"],
    "reasoning_long":   ["qwen2.5:14b-instruct-32k",   "qwen3-coder:480b-cloud",          "openrouter/fusion",                       "deepseek/deepseek-v4-flash"],
    "deep_coder_long":  ["qwen3-coder:480b-cloud",     "openrouter/fusion",               "nvidia/llama-3.1-nemotron-nano-8b-v1",   "deepseek/deepseek-v4-flash"],
    "coder_long":       ["qwen3-coder:480b-cloud",     "openrouter/fusion",               "deepseek/deepseek-v4-flash"],
}"""

src = re.sub(r'# ORDER.*?^}', new_pool, src, flags=re.DOTALL | re.MULTILINE)
with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('ROLE_POOL updated — Ollama 480b -> OpenRouter fusion -> NVIDIA -> DeepSeek V4-Flash')
