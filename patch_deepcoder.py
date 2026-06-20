path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\control_plane\shared_model_registry.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '    "deep_coder_long":  ["qwen2.5-coder:14b-32k",    "nvidia/llama-3.3-nemotron-super-49b-v1"],'
new = '    "deep_coder_long":  ["qwen2.5-coder:14b-32k",    "meta/llama-3.1-8b-instruct",             "nvidia/llama-3.3-nemotron-super-49b-v1"],'

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched OK')
else:
    print('ERROR: not found')
