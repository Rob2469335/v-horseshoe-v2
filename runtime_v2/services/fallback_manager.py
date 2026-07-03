import time
import logging
import asyncio
import os
import httpx
from swarm_os.config.settings import settings

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)

_CACHE_TTL = 1800
_last_fetch_time = 0
_cached_fallbacks = []
_cached_stats = {"openrouter": 0, "groq": 0, "gemini": 0, "nvidia": 0, "ollama": 0, "total": 0}

async def _fetch_openrouter_models() -> list[dict]:
    models = []
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=settings.ssl_verify) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models")
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for m in data:
                pricing = m.get("pricing", {})
                if pricing.get("prompt") == "0" and pricing.get("completion") == "0":
                    models.append({
                        "model": f"openrouter/{m['id']}",
                        "context_length": m.get("context_length", 8192),
                        "pricing": "Free",
                        "provider": "OpenRouter"
                    })
    except Exception as e:
        log.warning(f"Failed to fetch OpenRouter models: {e}")
    return models

async def _fetch_groq_models() -> list[dict]:
    models = []
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return models
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=settings.ssl_verify) as client:
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for m in data:
                m_id = m.get("id", "")
                if "canopylabs" in m_id or "whisper" in m_id:
                    continue
                models.append({
                    "model": f"groq/{m_id}",
                    "context_length": 8192,
                    "pricing": "API",
                    "provider": "Groq"
                })
    except Exception as e:
        log.warning(f"Failed to fetch Groq models: {e}")
    return models

async def _fetch_nvidia_models() -> list[dict]:
    models = []
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        return models
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=settings.ssl_verify) as client:
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = await client.get("https://integrate.api.nvidia.com/v1/models", headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for m in data:
                m_id = m.get("id", "")
                models.append({
                    "model": f"nvidia/{m_id}",
                    "context_length": 8192,
                    "pricing": "API",
                    "provider": "NVIDIA"
                })
    except Exception as e:
        log.warning(f"Failed to fetch NVIDIA models: {e}")
    return models

async def _fetch_gemini_models() -> list[dict]:
    models = []
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return models
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=settings.ssl_verify) as client:
            resp = await client.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}")
            resp.raise_for_status()
            data = resp.json().get("models", [])
            for m in data:
                name = m.get("name", "")
                supported_methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in supported_methods:
                    models.append({
                        "model": f"gemini/{name.replace('models/', '')}",
                        "context_length": 1048576,
                        "pricing": "API",
                        "provider": "Google"
                    })
    except Exception as e:
        log.warning(f"Failed to fetch Gemini models: {e}")
    return models

async def _fetch_ollama_models() -> list[dict]:
    models = []
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://127.0.0.1:11434/api/tags")
            if resp.status_code == 200:
                data = resp.json().get("models", [])
                for m in data:
                    models.append({
                        "model": f"ollama/{m['name']}",
                        "context_length": 8192,
                        "pricing": "Local",
                        "provider": "Ollama"
                    })
    except Exception:
        pass
    return models

async def refresh_fallbacks_if_needed():
    global _last_fetch_time, _cached_fallbacks, _cached_stats

    current_time = time.time()
    if current_time - _last_fetch_time < _CACHE_TTL and _cached_fallbacks:
        return

    log.debug("Refreshing fallback arrays (30-min TTL expired)...")

    results = await asyncio.gather(
        _fetch_openrouter_models(),
        _fetch_groq_models(),
        _fetch_nvidia_models(),
        _fetch_gemini_models(),
        _fetch_ollama_models(),
        return_exceptions=True
    )

    openrouter_models = results[0] if isinstance(results[0], list) else []
    groq_models = results[1] if isinstance(results[1], list) else []
    nvidia_models = results[2] if isinstance(results[2], list) else []
    gemini_models = results[3] if isinstance(results[3], list) else []
    ollama_models = results[4] if isinstance(results[4], list) else []

    groq_models.sort(key=lambda x: "70b" not in x["model"].lower() and "versatile" not in x["model"].lower(), reverse=False)
    nvidia_models.sort(key=lambda x: "70b" not in x["model"].lower() and "nemotron" not in x["model"].lower(), reverse=False)
    gemini_models.sort(key=lambda x: "pro" not in x["model"].lower(), reverse=False)
    openrouter_models.sort(key=lambda x: "70b" not in x["model"].lower(), reverse=False)

    valid_ollama = []
    for o in ollama_models:
        name = o["model"].lower()
        if "/" in o["model"] or "embed" in name or "rerank" in name or "vl" in name or "moondream" in name:
            continue
        valid_ollama.append(o)

    valid_ollama.sort(key=lambda x: "tool" not in x["model"].lower() and "coder" not in x["model"].lower(), reverse=False)

    all_fallbacks = []
    all_fallbacks.extend(valid_ollama[:3])
    all_fallbacks.extend(groq_models[:2])
    all_fallbacks.extend(nvidia_models[:2])
    all_fallbacks.extend(gemini_models[:2])
    all_fallbacks.extend(openrouter_models[:2])
    _cached_fallbacks = all_fallbacks
    _cached_stats = {
        "openrouter": len(openrouter_models),
        "groq": len(groq_models),
        "gemini": len(gemini_models),
        "nvidia": len(nvidia_models),
        "ollama": len(valid_ollama),
        "total": len(all_fallbacks),
    }
    _last_fetch_time = current_time

async def get_live_fallbacks(mode: str = "auto") -> list[dict]:
    await refresh_fallbacks_if_needed()

    local_models = [f for f in _cached_fallbacks if str(f.get("model", "")).startswith("ollama/")]
    cloud_models = [f for f in _cached_fallbacks if not str(f.get("model", "")).startswith("ollama/")]

    if mode == "local_only":
        return local_models

    if mode == "cloud_allowed":
        return local_models + cloud_models

    return local_models + cloud_models

def get_fallback_stats() -> dict:
    return _cached_stats




