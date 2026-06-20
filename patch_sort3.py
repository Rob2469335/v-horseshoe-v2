import re
path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

start = src.find('def sort_model_pool_by_provider_policy')
end = src.find('\ndef ', start + 1)
old_func = src[start:end]

new_func = """def sort_model_pool_by_provider_policy(models: List[str]) -> List[str]:
    # Preserves ROLE_POOL order: Ollama -> OpenRouter free/fusion -> NVIDIA free -> OpenRouter paid
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
    return result

"""

src = src[:start] + new_func + src[end:]
with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done')
