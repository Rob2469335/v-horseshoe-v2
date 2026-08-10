import time
import random
import logging
import asyncio
import os
import re
import threading
import httpx
from swarm_os.config.settings import settings

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)

_CACHE_TTL = 1800
_last_fetch_time = 0
_cached_fallbacks = []
# Which routing mode the cache currently holds. The local_only fetch produces a
# DIFFERENT chain (llama only) than auto/cloud_allowed (full cloud chain); a
# single time-TTL key would let one mode's cache serve the other for the whole
# 30-min window. The mode is part of the cache identity: reuse requires a match.
_cached_mode: str | None = None
_cached_stats = {"deepseek_direct": 0, "openrouter": 0, "groq": 0, "gemini": 0, "nvidia": 0, "llama": 0, "ollama": 0, "total": 0}
_refresh_lock = asyncio.Lock()

# UPGRADE: pooled httpx client reused across all provider probes instead of a
# fresh AsyncClient per call (fresh clients defeat keep-alive + TLS reuse).
_http_client: httpx.AsyncClient | None = None


_client_lock = asyncio.Lock()

async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    async with _client_lock:
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
_cooldown_sync_lock = threading.Lock()
_COOLDOWN_BASE_S = 30.0
_MAX_COOLDOWN_S = 600.0


# Permanent (non-retryable) failure markers — a model/provider that hits one of
# these will never recover by retrying the same request, so we jump straight to
# the max cooldown instead of burning the retry budget (retry research: only
# retry transient failures — timeouts/429/5xx; never 400/401/402/403/404).
#
# Two marker classes, matched differently:
#   - STATUS CODES are matched as STANDALONE tokens (word boundaries). Substring
#     matching was false-positive: "connection timeout after 4040ms" contained
#     the substring "404" and was misclassified as a permanent 404-not-found.
#     \b404\b distinguishes the string "404" AS AN HTTP status from digits that
#     merely happen to appear inside a longer number.
#   - TEXT phrases ("payment", "invalid api key", ...) stay substring-matched.
_NUMERIC_PERMANENT_STATUS = (401, 402, 403, 404)
# The status token must NOT be immediately followed by "ms" (a millisecond
# measure) — "timeout after 404 ms" contains the digits but no HTTP status,
# while "request failed after 404ms with status 404" still flags on its real
# standalone 404. Excluding the whole string on "ms" (a substring check) was
# too broad: it masked a genuine 404 that merely co-occurred with timing info.
_NUMERIC_PERMANENT_RE = re.compile(
    r"\b(?:%s)\b(?!\s*ms\b)" % "|".join(str(c) for c in _NUMERIC_PERMANENT_STATUS)
)
_TEXT_PERMANENT_MARKERS = (
    "payment", "insufficient credits", "no credits", "billing",
    "requires more credits", "insufficient balance",
    "invalid api key", "auth", "forbidden", "not found",
)


def is_permanent_error(error: str) -> bool:
    """True if the error string indicates a failure that retrying cannot fix
    (billing/auth/forbidden/missing resource). Callers should fail fast to
    fallback rather than re-issue the identical doomed request.

    Numeric status codes are matched as standalone tokens (\b401\b) so a
    transient error whose text merely CONTAINS those digits ("timeout after
    4040ms") is not falsely pinned as permanent; text phrases match as
    substrings."""
    err = str(error or "").lower()
    if _NUMERIC_PERMANENT_RE.search(err):
        return True
    return any(marker in err for marker in _TEXT_PERMANENT_MARKERS)


def _cooldown_key(model: str) -> str:
    """Cooldown key = the FULL model id. (Previously we took the suffix after the
    last ':' so every openrouter/*:free model keyed to 'free' and shared one
    cooldown — one failure pinned ALL free models; one success cleared them all.)"""
    return (model or "").strip()


def record_model_failure(model: str, error: str = "", permanent: bool | None = None) -> None:
    """Called when a model/providers generation fails (tool decision error, JSON repair,
    circuit-breaker trip). Marks the model down for an escalating cooldown window.
    Backoff uses exponential growth + jitter so synchronized failures (thundering
    herd) don't all retry on the same schedule (self-healing best practice)."""
    key = _cooldown_key(model)
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
            # so the provider is skipped until a human tops up and manually clears
            # it via `clear_model_cooldown` (documented AGENTS.md contract: the pin
            # exists so a billing problem surfaces as a visible, human-intervened
            # state — never auto-retried, which could burn attempts on a
            # definitively-broken key or silently succeed part of the time and mask
            # the billing issue).
            backoff = _MAX_COOLDOWN_S
            entry["until"] = float('inf')
        else:
            # Clamp the exponent: 2 ** (failures-1) overflows to a huge int past
            # failures>=1024, which then overflows the float multiplication BEFORE
            # min() can cap it (a crashed cooldown would break fallback forever).
            exponent = min(entry["failures"] - 1, 16)
            backoff = min(_MAX_COOLDOWN_S, _COOLDOWN_BASE_S * (2 ** exponent))
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
    key = _cooldown_key(model)
    return _is_cooldown_active(key)


def record_model_success(model: str) -> None:
    """Called on a clean generation — clears any cooldown so the model can be used again.

    NEVER clears a PERMANENT pin (`until == inf`, set on billing/auth 401/402/403
    by record_model_failure). A permanent pin encodes "do not auto-retry until a
    human tops up and clears it via clear_model_cooldown" — an in-flight request
    that happened to succeed before the pin was written (or a stale success
    event racing the failure) must not silently un-pin a doomed provider, or the
    billing problem disappears and the chain starts retrying the broken key.
    Only a transient (finite-window) cooldown is cleared on success."""
    key = _cooldown_key(model)
    if not key:
        return
    with _cooldowns_lock_sync():
        entry = _cooldowns.get(key)
        if entry is None:
            return
        if entry.get("until") == float("inf"):
            log.debug("Success NOT clearing permanent pin for %s (requires clear_model_cooldown)", key)
            return
        _cooldowns.pop(key, None)


def clear_model_cooldown(model: str) -> bool:
    """Manually clear the cooldown for ONE specific model (e.g. after a human
    tops up a previously billing-402'd account). Scoped to the exact model so a
    deliberate permanent pin is lifted without touching the legitimate
    exponential-backoff cooldowns of OTHER transiently-rate-limited models.
    Returns True if an entry was cleared, False if there was nothing to clear."""
    key = _cooldown_key(model)
    if not key:
        return False
    with _cooldowns_lock_sync():
        existed = key in _cooldowns
        if existed:
            _cooldowns.pop(key, None)
            log.info("Cooldown manually cleared for model %s", key)
    return existed


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
        client = await get_http_client()
        resp = await client.get("https://openrouter.ai/api/v1/models")
        resp.raise_for_status()
        data = resp.json().get("data", [])
        for m in data:
            pricing = m.get("pricing", {})
            m_id = m.get("id", "")
            if any(forbidden in m_id.lower() for forbidden in ("claude", "anthropic", "sonnet", "opus", "gpt-4")):
                continue
            # FLASH-ONLY policy: DeepSeek models are allowed only as v4-flash
            # variants. deepseek-v4-pro is 3x the flash price ($0.435/$0.87 vs
            # $0.14/$0.28) and legacy deepseek-chat/r1 aliases retired 2026-07-24.
            if "deepseek" in m_id.lower() and "flash" not in m_id.lower():
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
                "model": "openrouter/deepseek/deepseek-v4-flash-0731",
                "context_length": 1048576,
                "pricing": "Free",
                "provider": "OpenRouter",
            },
            {
                "model": "openrouter/deepseek/deepseek-v4-flash",
                "context_length": 1048576,
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
        client = await get_http_client()
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
        client = await get_http_client()
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = await client.get("https://integrate.api.nvidia.com/v1/models", headers=headers)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        for m in data:
            m_id = m.get("id", "")
            # FLASH-ONLY policy (same as OpenRouter): keep deepseek-v4-flash, drop
            # deepseek-v4-pro ($0.435/$0.87 — 3x flash) and coder variants.
            if "deepseek" in m_id.lower() and "flash" not in m_id.lower():
                continue
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
        client = await get_http_client()
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

_ZEN_FREE_BASE = "https://opencode.ai/zen/v1"      # OpenCode Zen FREE tier
_GO_PAID_BASE = "https://opencode.ai/zen/go/v1"    # OpenCode Go PAID tier

def _is_local_model(model_id: str) -> bool:
    """True only for local llama.cpp models served via openai/{local_name}."""
    if model_id.startswith("openai/"):
        name = model_id.replace("openai/", "").lower()
        if name.startswith("gpt") or name.startswith("o1") or name.startswith("o3") or "deepseek" in name:
            return False
        if name.startswith("zen/"):
            return False
        return True
    return False


def _get_opencode_fallback() -> list[dict]:
    """OpenCode cloud tiers, FREE first then PAID, both deepseek-v4-flash only.

    The user funded the OpenCode Go (paid) account, so the chain leads with the
    genuinely $0 options then the paid flash — and ONLY v4-flash (no GLM/Kimi/
    Qwen/pro):
      1. OpenCode Zen FREE deepseek-v4-flash ($0, https://opencode.ai/zen/v1)
      2. OpenCode Go PAID deepseek-v4-flash  (https://opencode.ai/zen/go/v1)
    `openai/zen/...` routes to the free Zen base; `openai/deepseek-v4-flash`
    routes to the paid Go base via OPENAI_API_BASE. No-op when OPENAI_API_KEY
    is absent.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []
    api_base = os.getenv("OPENAI_API_BASE", _GO_PAID_BASE)
    if "opencode" not in api_base.lower() and "openai" not in api_base.lower():
        return []
    return [
        # 1) OpenCode Zen FREE deepseek-v4-flash — $0.
        {"model": "openai/zen/deepseek-v4-flash", "context_length": 1048576, "pricing": "Free", "provider": "OpenCode Zen"},
        # 2) OpenCode Go PAID deepseek-v4-flash — the funded workhorse.
        {"model": "openai/deepseek-v4-flash", "context_length": 1048576, "pricing": "Paid (Go)", "provider": "OpenCode Go"},
    ]

def _get_deepseek_direct_fallback() -> list[dict]:
    """DeepSeek V4 Flash direct (first-party api.deepseek.com) — PRIMARY cloud
    model when DEEPSEEK_API_KEY is set. Cheapest path available: $0.14/M input
    (miss), $0.0028/M (cache hit, ~98% cheaper), $0.28/M output. litellm's
    native `deepseek/` provider routes to api.deepseek.com using DEEPSEEK_API_KEY.
    Returns [] (no-op) when the key is absent so the swarm keeps its current
    OpenRouter/free-tier chain."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return []
    return [{
        "model": "deepseek/deepseek-v4-flash",
        "context_length": 65536,
        "pricing": "Paid (cheap)",
        "provider": "DeepSeek Direct",
    }]

def _get_deepseek_openrouter_fallback() -> list[dict]:
    """OpenRouter-hosted DeepSeek V4 Flash — guaranteed DeepSeek fallback even if
    the live model catalog fetch fails. OpenRouter routes DeepSeek across ~22
    upstream providers, so it is the most-available single DeepSeek endpoint.
    Includes the 0731 build at $0.09/$0.18/M (36% cheaper than direct) plus the
    base flash at $0.14/$0.28. No-op when OPENROUTER_API_KEY is absent."""
    if not os.getenv("OPENROUTER_API_KEY"):
        return []
    return [
        {
            "model": "openrouter/deepseek/deepseek-v4-flash-0731",
            "context_length": 1048576,
            "pricing": "Paid (cheap)",
            "provider": "OpenRouter (DeepSeek)",
        },
        {
            "model": "openrouter/deepseek/deepseek-v4-flash",
            "context_length": 1048576,
            "pricing": "Paid (cheap)",
            "provider": "OpenRouter (DeepSeek)",
        },
    ]

def _get_ling_flash_fallback() -> list[dict]:
    """OpenRouter-hosted InclusionAI Ling — the ultra-cheap worker tier.

    Ling-2.6-flash (104B MoE / 7.4B active / 256K ctx) is $0.01 input / $0.03
    output per 1M tokens — roughly 14x cheaper than DeepSeek V4 Flash input and
    ~6x cheaper on output — ideal for high-volume routing/classification fan-out.
    Also includes ling-3.0-flash:free ($0) as a no-cost lead-in. Guaranteed even
    if the live catalog fetch fails (the catalog filter only keeps $0 models).
    No-op when OPENROUTER_API_KEY is absent."""
    if not os.getenv("OPENROUTER_API_KEY"):
        return []
    return [
        {
            "model": "openrouter/inclusionai/ling-3.0-flash:free",
            "context_length": 262144,
            "pricing": "Free",
            "provider": "OpenRouter (Ling)",
        },
        {
            "model": "openrouter/inclusionai/ling-2.6-flash",
            "context_length": 262144,
            "pricing": "Paid (ultra-cheap)",
            "provider": "OpenRouter (Ling)",
        },
    ]

async def _fetch_llama_models() -> list[dict]:
    models = []
    try:
        client = await get_http_client()
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
    global _last_fetch_time, _cached_fallbacks, _cached_stats, _cached_mode

    current_time = time.time()
    if current_time - _last_fetch_time < _CACHE_TTL and _cached_fallbacks and _cached_mode == mode:
        return

    async with _refresh_lock:
        if time.time() - _last_fetch_time < _CACHE_TTL and _cached_fallbacks and _cached_mode == mode:
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
        # NVIDIA free tier hosts deepseek-v4-flash — prioritize it (and any deepseek
        # model) so the cheapest-and-fast analysis model leads the NVIDIA batch.
        nvidia_models.sort(key=lambda x: (
            "deepseek" not in x["model"].lower(),       # deepseek models first
            "flash" not in x["model"].lower(),          # flash before pro/coder
            "70b" in x["model"].lower(),
        ))
        gemini_models.sort(key=lambda x: ("pro" in x["model"].lower(),))
        # Prefer DeepSeek models in the fetched OpenRouter batch (they are the cheap
        # sanctioned analysis models); remaining free/cheap models follow.
        openrouter_models.sort(key=lambda x: (
            "deepseek" not in x["model"].lower(),      # deepseek models first
            "70b" in x["model"].lower(),
        ))

        valid_llama = []
        for o in llama_models:
            name = o["model"].lower().split("/", 1)[-1]
            if "embed" in name or "rerank" in name or "vl" in name or "moondream" in name:
                continue
            valid_llama.append(o)

        valid_llama.sort(key=lambda x: ("tool" in x["model"].lower(), "coder" in x["model"].lower()))

        all_fallbacks = []
        # Cloud chain order (researched 2026-08): the three deepseek-v4-flash options
        # lead INLINE (FREE first, then the funded OpenCode Go account), then
        # free/cheap backups, then OpenRouter, and paid DeepSeek direct LAST.
        #
        # 1) NVIDIA free NIM v4-flash — $0 free tier.
        all_fallbacks.extend(nvidia_models[:1])
        # 2) OpenCode Zen FREE deepseek-v4-flash ($0) then OpenCode Go PAID
        #    deepseek-v4-flash (funded account), then the rest of the Go models.
        _opencode_models = _get_opencode_fallback()
        if _opencode_models:
            all_fallbacks.extend(_opencode_models)
        # 3) Groq / Gemini free tiers (non-DeepSeek backup clouds).
        all_fallbacks.extend(groq_models[:2])
        all_fallbacks.extend(gemini_models[:1])
        # 4) Ling ultra-cheap worker tier ($0.01/$0.03, plus free ling-3.0 lead-in)
        #    for high-volume routing/classification fan-out.
        _ling = _get_ling_flash_fallback()
        if _ling:
            all_fallbacks.extend(_ling)
        # 5) OpenRouter — free-credit last resort (OpenRouter-hosted DeepSeek first,
        #    then other cheap/free models).
        _deepseek_or = _get_deepseek_openrouter_fallback()
        if _deepseek_or:
            all_fallbacks.extend(_deepseek_or)
        all_fallbacks.extend(openrouter_models[:3])
        # 6) DeepSeek DIRECT (paid api.deepseek.com) — LAST resort.
        _deepseek_direct = _get_deepseek_direct_fallback()
        if _deepseek_direct:
            all_fallbacks.extend(_deepseek_direct)
        all_fallbacks.extend(valid_llama[:2])

        # Dedup by model id (the guaranteed OpenRouter DeepSeek entries can also
        # appear in the fetched openrouter_models batch).
        seen = set()
        deduped = []
        for f in all_fallbacks:
            mid = f.get("model", "")
            if mid in seen:
                continue
            seen.add(mid)
            deduped.append(f)
        all_fallbacks = deduped
        _cached_fallbacks = all_fallbacks
        _cached_stats = {
            "deepseek_direct": len(_deepseek_direct),
            "openrouter": len(openrouter_models),
            "groq": len(groq_models),
            "gemini": len(gemini_models),
            "nvidia": len(nvidia_models),
            "llama.cpp": len(valid_llama),
            "total": len(all_fallbacks),
        }
        _last_fetch_time = current_time
        _cached_mode = mode


async def get_live_fallbacks(mode: str = "auto") -> list[dict]:
    """Return the live fallback chain, refreshing the cache if stale.

    Filters out models currently in cooldown (failed recently) so the caller
    never retried a known-bad model. Callers import this — it was missing,
    causing `ImportError: cannot import name 'get_live_fallbacks'` at every
    tool-decision site (stream_runner, _llm_client, agents, chat_service).
    """
    await refresh_fallbacks_if_needed(mode=mode)
    live: list[dict] = []
    for f in _cached_fallbacks:
        mid = f.get("model", "")
        if mid and is_model_cooled_down(mid):
            continue
        live.append(f)
    return live

