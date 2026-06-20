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
            nvidia_models.append(m)
        elif provider == "gemini" and gemini_key:
            gemini_models.append(m)
        elif provider == "ollama":
            ollama_models.append(m)
        else:
            other_models.append(m)
    return nvidia_models + openrouter_models + ollama_models + other_models"""

new = """def sort_model_pool_by_provider_policy(models: List[str]) -> List[str]:
    # Preserves ROLE_POOL order: Ollama -> OpenRouter free -> NVIDIA free -> OpenRouter paid
    openrouter_key = _os.environ.get("OPENROUTER_API_KEY", "").strip()
    nvidia_key = _os.environ.get("NVIDIA_API_KEY", "").strip()
    result = []
    for m in models:
        provider = _provider_for_model(m)
        if provider == "ollama":
            result.append(m)
        elif provider == "openrouter" and openrouter_key:
            result.append(m)
        elif provider == "nvidia" and nvidia_key:
            result.append(m)
    return result"""

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched — sort now preserves ROLE_POOL order')
else:
    print('ERROR: not found')
