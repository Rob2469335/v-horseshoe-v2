path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\control_plane\shared_model_registry.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '    # OpenRouter free tier'
new = """    # OpenRouter auto-router (picks best free model for task automatically)
    ModelProfile(name="openrouter/fusion",                               role="cloud_auto",     capabilities=["general", "code", "reasoning", "cloud"],      metadata={"cloud": True, "provider": "openrouter"}),
    # OpenRouter paid — DeepSeek V4-Flash (best value, ~$0.10/1M tokens)
    ModelProfile(name="deepseek/deepseek-v4-flash",                      role="cloud_paid",     capabilities=["general", "code", "reasoning", "cloud"],      metadata={"cloud": True, "provider": "openrouter"}),
    # OpenRouter free tier"""

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('CLOUD_MODEL_SPECS updated')
else:
    print('ERROR: not found')
