path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\control_plane\shared_model_registry.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

new_pool = """# ORDER MATTERS — NVIDIA big models first, OpenRouter fallback, Ollama local last.
ROLE_POOL = {
    "fast":             ["qwen2.5:3b-instruct"],
    "coder_small":      ["qwen2.5-coder:3b"],
    "general":          ["qwen2.5:7b-instruct",       "qwen2.5:3b-instruct",            "meta/llama-3.3-70b-instruct",             "qwen/qwen-2.5-72b-instruct:free"],
    "coder":            ["qwen2.5-coder:7b",           "qwen2.5-coder:14b",              "nvidia/llama-3.3-nemotron-super-49b-v1",  "mistralai/mistral-large-3-675b-instruct-2512"],
    "reasoning":        ["qwen2.5:14b-instruct",       "qwen3:14b",                      "nvidia/llama-3.1-nemotron-ultra-253b-v1", "deepseek-ai/deepseek-v4-pro"],
    "deep_coder":       ["qwen2.5-coder:14b",          "nvidia/llama-3.3-nemotron-super-49b-v1", "mistralai/codestral-22b-instruct-v0.1"],
    "planner":          ["qwen3:14b",                  "qwen2.5:14b-instruct",           "deepseek-ai/deepseek-v4-pro",             "nvidia/llama-3.1-nemotron-ultra-253b-v1"],
    "writer":           ["mistral-nemo:12b",            "mistralai/mistral-large-3-675b-instruct-2512", "writer/palmyra-creative-122b"],
    "vision":           ["qwen3-vl:8b",                "moondream:latest",               "meta/llama-3.2-90b-vision-instruct"],
    "embedding":        ["qwen3-embedding:8b",          "nomic-embed-text:latest"],
    "reranker":         ["qllama/bge-reranker-v2-m3:latest"],
    "cloud_heavy":      ["deepseek-ai/deepseek-v4-pro", "nvidia/llama-3.1-nemotron-ultra-253b-v1", "mistralai/mistral-large-3-675b-instruct-2512"],
    "cloud_general":    ["meta/llama-3.3-70b-instruct", "meta/llama-4-maverick-17b-128e-instruct"],
    "cloud_writer":     ["mistralai/mistral-large-3-675b-instruct-2512", "writer/palmyra-creative-122b"],
    "cloud_reasoning":  ["deepseek-ai/deepseek-v4-pro", "nvidia/llama-3.1-nemotron-ultra-253b-v1"],
    "cloud_coder":      ["nvidia/llama-3.3-nemotron-super-49b-v1", "mistralai/codestral-22b-instruct-v0.1", "mistralai/mistral-large-3-675b-instruct-2512"],
    "cloud_fast":       ["meta/llama-3.3-70b-instruct", "meta/llama-4-maverick-17b-128e-instruct"],
    "cloud_pro":        ["deepseek-ai/deepseek-v4-pro", "nvidia/llama-3.1-nemotron-ultra-253b-v1"],
    "reasoning_long":   ["qwen2.5:14b-instruct-32k",   "deepseek-ai/deepseek-v4-pro"],
    "deep_coder_long":  ["qwen2.5-coder:14b-32k",      "nvidia/llama-3.3-nemotron-super-49b-v1", "mistralai/codestral-22b-instruct-v0.1"],
    "coder_long":       ["qwen2.5-coder:7b-16k",       "nvidia/llama-3.3-nemotron-super-49b-v1"],
}"""

# Find and replace the entire ROLE_POOL block
import re
src = re.sub(r'# ORDER MATTERS.*?^}', new_pool, src, flags=re.DOTALL | re.MULTILINE)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('ROLE_POOL updated with big models only')
