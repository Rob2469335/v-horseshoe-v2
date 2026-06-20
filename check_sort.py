path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = """def sort_model_pool_by_provider_policy(models: List[str]) -> List[str]:
    openrouter_key = _os.environ.get("OPENROUTER_API_KEY", "").strip()
    nvidia_key = _os.environ.get("NVIDIA_API_KEY", "").strip()
    gemini_key = _os.environ.get("GEMINI_API_KEY", "").strip()
    openrouter_models = []
    nvidia_models = []
    gemini_models = []
    ollama_models = []
    other_models = []
    for m in models:
        provider = _provider_for_model(m)
        if provider == "openrouter" and openrouter_key:
            openrouter_models.append(m)
        elif provider == "nvidia" and nvidia_key:
            nvidia_models.append(m)"""

if old in src:
    print('FOUND - ready to patch')
else:
    # Print exact lines for diagnosis
    for i, line in enumerate(src.splitlines()[26:46], start=27):
        print(f'{i}: {repr(line)}')
