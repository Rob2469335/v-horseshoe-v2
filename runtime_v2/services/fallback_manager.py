import time
import logging
import asyncio
import os
import httpx

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)

# Global state for the 30-minute cache
_CACHE_TTL = 1800  # 30 minutes in seconds
_last_fetch_time = 0
_cached_fallbacks = []
_cached_stats = {"openrouter": 0, "groq": 0, "gemini": 0, "nvidia": 0, "total": 0}

# Hardcoded safe defaults in case the network fails entirely
SAFE_DEFAULTS = [
    "openrouter/meta-llama/llama-3.1-8b-instruct:free",
    "openrouter/mistralai/mistral-7b-instruct:free",
    "openrouter/google/gemini-flash-1.5:free",
    "groq/llama-3.3-70b-versatile",
    "groq/llama-3.1-8b-instant",
]

async def _fetch_openrouter_free() -> list[str]:
    """Scrape OpenRouter API for currently free models."""
    models = []
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models")
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for m in data:
                pricing = m.get("pricing", {})
                if pricing.get("prompt") == "0" and pricing.get("completion") == "0":
                    models.append(f"openrouter/{m['id']}")
    except Exception as e:
        log.warning(f"Failed to fetch OpenRouter free models: {e}")
    return models

async def _fetch_groq_models() -> list[str]:
    """Scrape Groq API for currently available models."""
    models = []
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return models
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for m in data:
                models.append(f"groq/{m['id']}")
    except Exception as e:
        log.warning(f"Failed to fetch Groq models: {e}")
    return models

async def refresh_fallbacks_if_needed():
    """Check TTL and refresh background lists if older than 30 minutes."""
    global _last_fetch_time, _cached_fallbacks, _cached_stats
    
    current_time = time.time()
    if current_time - _last_fetch_time < _CACHE_TTL and _cached_fallbacks:
        return  # Cache is still valid

    log.debug("Refreshing cloud fallback arrays (30-minute TTL expired)...")
    
    # Fetch all providers concurrently
    openrouter_models, groq_models = await asyncio.gather(
        _fetch_openrouter_free(),
        _fetch_groq_models()
    )
    
    # Assemble Groq static fallback models (Groq is fastest for fallback)
    groq_static = ["groq/llama-3.3-70b-versatile", "groq/llama-3.1-8b-instant"]
    gemini_models = []  # Gemini disabled — API key issues
    nvidia_models = []  # Nvidia disabled — bad provider prefix in LiteLLM
    
    # Construct fallback pool — Groq first (fastest), then OpenRouter
    all_fallbacks = []
    all_fallbacks.extend(groq_static)
    all_fallbacks.extend(groq_models)
    all_fallbacks.extend(openrouter_models)
    
    if not all_fallbacks:
        # Emergency fail-safe
        all_fallbacks = SAFE_DEFAULTS.copy()
        
    _cached_fallbacks = all_fallbacks
    _cached_stats = {
        "openrouter": len(openrouter_models),
        "groq": len(groq_models) + len(groq_static),
        "gemini": 0,
        "nvidia": 0,
        "total": len(all_fallbacks)
    }
    _last_fetch_time = current_time

async def get_live_fallbacks() -> list[dict]:
    """Get the live array of fallback models for LiteLLM."""
    await refresh_fallbacks_if_needed()
    return [{"model": m} for m in _cached_fallbacks]

def get_fallback_stats() -> dict:
    """Get the latest synchronous telemetry on the fallback array."""
    return _cached_stats
