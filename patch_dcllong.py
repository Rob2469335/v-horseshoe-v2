path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\control_plane\shared_model_registry.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '    "deep_coder_long":  ["qwen2.5-coder:14b-32k",      "nvidia/llama-3.3-nemotron-super-49b-v1", "mistralai/codestral-22b-instruct-v0.1"],'
new = '    "deep_coder_long":  ["deepseek-ai/deepseek-v4-pro", "nvidia/llama-3.1-nemotron-ultra-253b-v1", "nvidia/llama-3.3-nemotron-super-49b-v1", "qwen2.5-coder:14b-32k"],'

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('OK')
else:
    print('ERROR: not found')
