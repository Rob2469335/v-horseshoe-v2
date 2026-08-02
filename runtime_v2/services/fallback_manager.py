import time
import random
import logging
import asyncio
import os
import threading
import httpx
from swarm_os.config.settings import settings

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)

_CACHE_TTL = 1800
_last_fetch_time = 0
_cached_fallbacks = []
_cached_stats = {"openrouter": 0, "groq": 0, "gemini": 0, "nvidia": 0, "llama": 0, "ollama": 0, "total": 0}
_refresh_lock = asyncio.Lock()

# UPGRADE: pooled httpx client reused across all provider probes instead of a
# fresh AsyncClient per call (fresh clients defeat keep-alive + TLS reuse).
_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            verify=settings.ssl_verify,
        )
    return _http_client

# Outcome-driven cooldowns (upgrade: skip a failing model BEFORE the call, not after).
# model_name -> {"failures": int, "until": float, "last_error": str}
_cooldowns: dict[str, dict] = {}
_cooldown_lock = asyncio.Lock()
_cooldown_sync_lock = threading.Lock()
_COOLDOWN_BASE_S = 30.0
_MAX_COOLDOWN_S = 600.0


# Permanent (non-retryable) failure markers — a model/provider that hits one of
# these will never recover by retrying the same request, so we jump straight to
# the max cooldown instead of burning the retry budget (retry research: only
# retry transient failures — timeouts/429/5xx; never 400/401/402/403/404).
_PERMANENT_ERROR_MARKERS = (
    "402", "payment", "insufficient credits", "no credits", "billing",
    "requires more credits", "401", "403", "404", "invalid api key",
    "auth", "forbidden", "not found",
)


def is_permanent_error(error: str) -> bool:
    """True if the error string indicates a failure that retrying cannot fix
    (billing/auth/forbidden/missing resource). Callers should fail fast to
    fallback rather than re-issue the identical doomed request."""
    err = str(error or "").lower()
    return any(marker in err for marker in _PERMANENT_ERROR_MARKERS)


def record_model_failure(model: str, error: str = "", permanent: bool | None = None) -> None:
    """Called when a model/providers generation fails (tool decision error, JSON repair,
    circuit-breaker trip). Marks the model down for an escalating cooldown window.
    Backoff uses exponential growth + jitter so synchronized failures (thundering
    herd) don't all retry on the same schedule (self-healing best practice)."""
    key = (model or "").split(":", 1)[-1]
    if not key:
        return
    now = time.time()
    if permanent is None:
        permanent = is_permanent_error(error)
    with _cooldowns_lock_sync():
        entry = _cooldowns.setdefault(key, {"failures": 0, "until": 0.0, "last_error": ""})
        entry["failures"] += 1
        if permanent:
            # Payment/auth failures won't clear on their own — pin at max cooldown
            # so the provider is skipped until it is explicitly cleared by success.
            backoff = _MAX_COOLDOWN_S
            entry["until"] = now + backoff
        else:
            backoff = min(_MAX_COOLDOWN_S, _COOLDOWN_BASE_S * (2 ** (entry["failures"] - 1)))
            backoff *= random.uniform(0.75, 1.25)  # ±25% jitter
            entry["until"] = now + backoff
        entry["last_error"] = str(error)[:200]
        log.warning(
            "Model %s marked down for %.0fs (failure #%d%s): %s",
            key, backoff, entry["failures"], " permanent" if permanent else "", str(error)[:80],
        )


def is_model_cooled_down(model: str) -> bool:
    """Public check — used by the router so routing decisions skip a provider that
    is already in a cooldown window (prevents re-selecting a known-failing model)."""
    key = (model or "").split(":", 1)[-1]
    return _is_cooldown_active(key)


def record_model_success(model: str) -> None:
    """Called on a clean generation — clears any cooldown so the model can be used again."""
    key = (model or "").split(":", 1)[-1]
    if not key:
        return
    with _cooldowns_lock_sync():
        _cooldowns.pop(key, None)


def _cooldowns_lock_sync():
    return _cooldown_sync_lock


def _is_cooldown_active(key: str) -> bool:
    now = time.time()
    with _cooldowns_lock_sync():
        entry = _cooldowns.get(key)
        if not entry:
            return False
        if now >= entry["until"]:
            _cooldowns.pop(key, None)
            return False
        return True

async def _fetch_openrouter_models() -> list[dict]:
    models = []
    try:
        client = get_http_client()
        resp = await client.get("https://openrouter.ai/api/v1/models")
        resp.raise_for_status()
        data = resp.json().get("data", [])
        for m in data:
            pricing = m.get("pricing", {})
            m_id = m.get("id", "")
            if any(forbidden in m_id.lower() for forbidden in ("claude", "anthropic", "sonnet", "opus", "gpt-4")):
                continue
            if (pricing.get("prompt") == "0" and pricing.get("completion") == "0") or ":free" in m_id.lower() or "deepseek" in m_id.lower():
                models.append({
                    "model": f"openrouter/{m['id']}",
                    "context_length": m.get("context_length", 65536),
                    "pricing": "Free",
                    "provider": "OpenRouter"
                })
    except Exception as e:
        log.warning(f"Failed to fetch OpenRouter models: {e}")
    if not models:
        models = [
            {
                "model": "openrouter/deepseek/deepseek-r1:free",
                "context_length": 65536,
                "pricing": "Free",
                "provider": "OpenRouter",
            },
            {
                "model": "openrouter/deepseek/deepseek-chat:free",
                "context_length": 65536,
                "pricing": "Free",
                "provider": "OpenRouter",
            },
            {
                "model": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                "context_length": 65536,
                "pricing": "Free",
                "provider": "OpenRouter",
            },
            {
                "model": "openrouter/qwen/qwen-2.5-coder-32b-instruct:free",
                "context_length": 65536,
                "pricing": "Free",
                "provider": "OpenRouter",
            },
        ]
    return models

async def _fetch_groq_models() -> list[dict]:
    models = []
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return models
    try:
        client = get_http_client()
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
    os.environ.setdefault("NVIDIA_NIM_API_KEY", api_key)
    try:
        client = get_http_client()
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = await client.get("https://integrate.api.nvidia.com/v1/models", headers=headers)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        for m in data:
            m_id = m.get("id", "")
            models.append({
                "model": f"nvidia_nim/{m_id}",
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
        client = get_http_client()
        headers = {"x-goog-api-key": api_key}
        resp = await client.get("https://generativelanguage.googleapis.com/v1beta/models", headers=headers)
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

async def _fetch_llama_models() -> list[dict]:
    models = []
    try:
        client = get_http_client()
        resp = await client.get("http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer llama"})
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for m in data:
                models.append({
                    "model": f"openai/{m['id']}",
                    "context_length": 32768,
                    "pricing": "Local",
                    "provider": "llama.cpp"
                })
    except Exception:
        log.warning("Failed to fetch llama.cpp models, is llama-server running?")
    return models

async def refresh_fallbacks_if_needed(mode: str = "auto"):
    global _last_fetch_time, _cached_fallbacks, _cached_stats

    current_time = time.time()
    if current_time - _last_fetch_time < _CACHE_TTL and _cached_fallbacks:
        return

    async with _refresh_lock:
        if time.time() - _last_fetch_time < _CACHE_TTL and _cached_fallbacks:
            return
            
        log.debug("Refreshing fallback arrays (30-min TTL expired)...")

    if mode == "local_only":
        results = await asyncio.gather(
            _fetch_llama_models(),
            return_exceptions=True
        )
        results = [[], [], [], [], results[0]]
    else:
        results = await asyncio.gather(
            _fetch_openrouter_models(),
            _fetch_groq_models(),
            _fetch_nvidia_models(),
            _fetch_gemini_models(),
            _fetch_llama_models(),
            return_exceptions=True
        )

    openrouter_models = results[0] if isinstance(results[0], list) else []
    groq_models = results[1] if isinstance(results[1], list) else []
    nvidia_models = results[2] if isinstance(results[2], list) else []
    gemini_models = results[3] if isinstance(results[3], list) else []
    llama_models = results[4] if isinstance(results[4], list) else []

    groq_models.sort(key=lambda x: ("70b" in x["model"].lower(), "versatile" in x["model"].lower()))
    nvidia_models.sort(key=lambda x: ("70b" in x["model"].lower(), "nemotron" in x["model"].lower()))
    gemini_models.sort(key=lambda x: ("pro" in x["model"].lower(),))
    openrouter_models.sort(key=lambda x: ("70b" in x["model"].lower(),))

    valid_llama = []
    for o in llama_models:
        name = o["model"].lower().split("/", 1)[-1]
        if "embed" in name or "rerank" in name or "vl" in name or "moondream" in name:
            continue
        valid_llama.append(o)

    valid_llama.sort(key=lambda x: ("tool" in x["model"].lower(), "coder" in x["model"].lower()))

    all_fallbacks = []
    all_fallbacks.extend(valid_llama[:3])
    all_fallbacks.extend(groq_models[:2])
    all_fallbacks.extend(nvidia_models[:2])
    all_fallbacks.extend(gemini_models[:2])
    all_fallbacks.extend(openrouter_models[:5])
    _cached_fallbacks = all_fallbacks
    _cached_stats = {
        "openrouter": len(openrouter_models),
        "groq": len(groq_models),
        "gemini": len(gemini_models),
        "nvidia": len(nvidia_models),
        "llama.cpp": len(valid_llama),
        "total": len(all_fallbacks),
    }
    _last_fetch_time = current_time

async def get_live_fallbacks(mode: str = "auto") -> list[dict]:
    await refresh_fallbacks_if_needed(mode)

    # BUG/UPGRADE: skip models currently in a cooldown window (recorded via
    # record_model_failure on JSON-parse errors, timeouts, or breaker trips).
    def _not_cooled(f: dict) -> bool:
        key = str(f.get("model", "")).split(":", 1)[-1]
        return not _is_cooldown_active(key)

    cached = [f for f in _cached_fallbacks if _not_cooled(f)]

    local_models = [f for f in cached if str(f.get("model", "")).startswith("openai/")]
    cloud_models = [f for f in cached if not str(f.get("model", "")).startswith("openai/")]

    if mode == "local_only":
        return local_models

    if mode == "cloud_allowed":
        return cloud_models + local_models

    # Auto mode: prioritize local
    return local_models + cloud_models

def get_fallback_stats() -> dict:
    return _cached_stats




