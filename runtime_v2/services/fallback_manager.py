import time
import logging
import asyncio
import os
import httpx

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)

# Global state for the 30-minute cache
_CACHE_TTL = 300  # 5 minutes in seconds
_last_fetch_time = 0
_cached_fallbacks = []
_cached_stats = {"openrouter": 0, "groq": 0, "gemini": 0, "nvidia": 0, "total": 0}

SAFE_DEFAULTS = [
    "openrouter/meta-llama/llama-3.1-8b-instruct:free",
    "openrouter/mistralai/mistral-7b-instruct:free",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "groq/llama-3.3-70b-versatile",
    "groq/llama-3.1-8b-instant",
]

async def _fetch_openrouter_free() -> list[dict]:
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
                    models.append({
                        "model": f"openrouter/{m['id']}",
                        "context_length": m.get("context_length", 8192),
                        "pricing": "Free",
                        "provider": "OpenRouter"
                    })
    except Exception as e:
        log.warning(f"Failed to fetch OpenRouter free models: {e}")
    return models

async def _fetch_groq_models() -> list[dict]:
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
                if 'canopylabs' in m['id']:
                    continue
                models.append({
                    "model": f"groq/{m['id']}",
                    "context_length": 8192, # Default fallback
                    "pricing": "Free",
                    "provider": "Groq"
                })
    except Exception as e:
        log.warning(f"Failed to fetch Groq models: {e}")
    return models

async def refresh_fallbacks_if_needed():
    """Check TTL and refresh background lists if older than cache TTL (optimized for speed)."""
    global _last_fetch_time, _cached_fallbacks, _cached_stats
    
    current_time = time.time()
    if current_time - _last_fetch_time < _CACHE_TTL and _cached_fallbacks:
        return  # Cache is still valid

    log.debug("Refreshing cloud fallback arrays (5-min TTL expired)...")
    
    # Fetch OpenRouter in parallel with Groq fallback (no waiting)
    try:
        # Use timeout to fail fast
        openrouter_models = await asyncio.wait_for(
            _fetch_openrouter_free(), 
            timeout=3.0  # Fail fast after 3s
        )
    except asyncio.TimeoutError:
        log.debug("OpenRouter fetch timed out, using safe defaults")
        openrouter_models = []
    except Exception as e:
        log.debug(f"Failed to fetch OpenRouter: {e}")
        openrouter_models = []
    
    # Minimal Groq static fallback (avoid rate limits)
    groq_static = [
        {"model": "groq/llama-3.1-8b-instant", "context_length": 8192, "pricing": "Free", "provider": "Groq"}
    ]
    
    # Gemini models (more reliable, cached)
    gemini_models = [
        {"model": "gemini/gemini-1.5-flash", "context_length": 1048576, "pricing": "Free Tier", "provider": "Google"},
    ]
    
    # Construct fallback pool — OpenRouter first (more reliable), then minimal Groq, then Gemini
    all_fallbacks = []
    all_fallbacks.extend(openrouter_models[:3])  # Limit to 3 OpenRouter models
    all_fallbacks.extend(groq_static)  # Only 1 Groq model
    all_fallbacks.extend(gemini_models)
    
    if not all_fallbacks:
        # Emergency fail-safe with minimal list
        all_fallbacks = [
            {"model": "groq/llama-3.1-8b-instant", "context_length": 8192, "pricing": "Free", "provider": "Groq"}
        ]
        
    _cached_fallbacks = all_fallbacks
    _cached_stats = {
        "openrouter": len(openrouter_models),
        "groq": len(groq_static),
        "gemini": len(gemini_models),
        "nvidia": 0,
        "total": len(all_fallbacks)
    }
    _last_fetch_time = current_time

async def get_live_fallbacks() -> list[dict]:
    """Get the live array of fallback models for LiteLLM."""
    await refresh_fallbacks_if_needed()
    return _cached_fallbacks

def get_fallback_stats() -> dict:
    """Get the latest synchronous telemetry on the fallback array."""
    return _cached_stats
