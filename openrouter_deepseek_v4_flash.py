"""
openrouter_deepseek_v4_flash.py
===============================

Thin, dependency-light client for DeepSeek V4 Flash served through OpenRouter
(no vendor SDK, just the standard library + ``httpx``).

WHY OPENROUTER
--------------
One API key covers DeepSeek plus any configured fallbacks, and OpenRouter adds
an *opt-in response cache* on top of DeepSeek's *automatic provider-side prompt
cache*. The two caches are independent and stack:

  * Prompt cache (DeepSeek provider side, automatic):
      DeepSeek caches the KV state for the *longest byte-identical prefix* of a
      request and reuses it on the next call. Cache hits are billed at a
      fraction of the uncached input rate and reported in
      ``usage.prompt_tokens_details.cached_tokens``. The cache is only worth
      chasing when the stable prefix is large -- roughly >= 1000 tokens -- so
      keep the system prompt, tool schemas and project context big and frozen.

  * Response cache (OpenRouter side, opt-in):
      OpenRouter stores the *complete response* keyed on an identical request
      (same model, messages, parameters). A HIT is billed at $0 -- no tokens at
      all. Only applies when two requests are byte-for-byte identical, so use
      it for deterministic workloads (FAQs, test harnesses, retries), not for
      dynamic conversation.

COST ENGINEERING (read this before paying for tokens)
------------------------------------------------------
Two routes are supported via the ``endpoint`` parameter:

  * ``endpoint="openrouter"`` (default): bills your OpenRouter credits.
    OpenRouter passes provider rates through with no markup and charges a 5.5%
    fee on credit purchases. The model lists at roughly $0.0896/M input /
    $0.1792/M output, served by whichever policy-allowed provider OpenRouter
    picks (first-party DeepSeek only if the account's privacy policy allows
    it). Response-cache HITs are free; prompt-cache hits receive a discount
    but NOT DeepSeek's full first-party cache rate.

  * ``endpoint="deepseek"``: hits the first-party API directly
    (https://api.deepseek.com/v1, model ``deepseek-v4-flash``) with
    ``DEEPSEEK_API_KEY``. Rates are $0.14/M cache-miss input, $0.0028/M
    cache-hit input (98% cheaper) and $0.28/M output. DeepSeek caches
    automatically on repeated prefixes, so a swarm with a large, frozen
    system+context prefix pays the $0.0028 rate for most of its input. This
    is the cheapest configuration for cache-heavy workloads; the trade-offs
    are no OpenRouter response cache, no multi-provider failover, and data
    handled by DeepSeek directly.

Cheapest configuration, in order:
  1. ``endpoint="deepseek"`` (direct) -- floor price, best cache rate.
  2. OpenRouter BYOK -- bind your DeepSeek key in the OpenRouter workspace;
     you keep one key + failover and bill your DeepSeek key at official rates
     (including $0.0028 cache hits). First 1M requests/month carry no fee.
  3. ``endpoint="openrouter"`` with credits -- most convenient, but most
     expensive for high-cache workloads.

HOW session_id HELPS
--------------------
Prompt caching only stays warm if the *same provider* serves every request in a
conversation; if OpenRouter bounces between providers the KV cache is cold on
every turn. ``session_id`` is sent both in the request body and as the
``x-session-id`` header, which activates OpenRouter's **sticky routing**: the
conversation is pinned to whichever provider serves the first successful
request, and every follow-up with the same ``session_id`` stays there (verified
live: 4/4 calls in one session stayed on a single provider). If that provider
goes down, sticky routing falls back to the next-best provider instead of
failing.

There are two additional knobs in this module, in order of increasing strictness:

  * ``provider_order=["DeepSeek"]`` -- preference only. First-party DeepSeek is
    tried first, but requests may still land on another allowed provider.
  * ``pin_provider=True`` -- hard pin to ``provider: {"only": ["DeepSeek"]}``.
    Zero provider drift and first-party cache reads at DeepSeek's 0.1x rate, but
    NO failover, and it only works if the account's privacy/guardrail policy
    allows the first-party DeepSeek endpoint. OpenRouter filters endpoints by
    policy *before* routing: accounts with Zero Data Retention (or a provider
    allowlist that excludes DeepSeek) get ``404 No endpoints available matching
    your guardrail restrictions and data policy`` -- the request then falls back
    to third-party hosts (DeepInfra, GMICloud, StreamLake, ...) which can change
    between calls unless sticky routing is active. To enable ``pin_provider``,
    allow the DeepSeek provider at ``https://openrouter.ai/settings/privacy``.

Only standard library modules plus ``httpx`` are imported. The module holds no
global state beyond a logger; the API key is always read from the
``OPENROUTER_API_KEY`` environment variable.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Any

import httpx

__all__ = [
    "OpenRouterError",
    "call_v4_flash",
    "acall_v4_flash",
    "build_messages",
    "log_usage",
    "BASE_URL",
    "MODEL",
]

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Configuration constants (safe to override per call via keyword arguments)
# --------------------------------------------------------------------------- #

BASE_URL = "https://openrouter.ai/api/v1"
CHAT_COMPLETIONS_URL = f"{BASE_URL}/chat/completions"
MODEL = "deepseek/deepseek-v4-flash"

# Endpoint configuration. ``endpoint="deepseek"`` is the cheapest option for
# cache-heavy workloads (automatic context caching bills cached input at
# ~$0.0028/M vs ~$0.14/M uncached, with no aggregator fee).
ENDPOINTS: dict[str, dict[str, str]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-v4-flash",
        "key_env": "OPENROUTER_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-flash",
        "key_env": "DEEPSEEK_API_KEY",
    },
}

# Default read/response timeout in seconds. DeepSeek can be slow to emit its
# first token on a cold KV cache, so this is generous by design.
DEFAULT_TIMEOUT_SECONDS = 120.0

# Total number of HTTP attempts before giving up on transient failures.
DEFAULT_MAX_RETRIES = 3

# HTTP status codes worth retrying (rate limits + transient 5xx).
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

# OpenRouter provider id for DeepSeek (used by provider pinning).
_DEEPSEEK_PROVIDER = "DeepSeek"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class OpenRouterError(Exception):
    """Raised for every failure path (network, HTTP status, malformed body).

    Carries structured context so a higher-level orchestrator can decide
    whether to retry, fail, or fall back to another model.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
        request_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body
        self.request_id = request_id
        self.cause = cause

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        if self.body is not None:
            parts.append(f"body={_snippet(self.body)}")
        return " | ".join(parts)


def _snippet(value: Any, limit: int = 500) -> str:
    """Human-readable, length-capped representation for error messages."""
    try:
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    except (TypeError, ValueError):
        text = str(value)
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


# --------------------------------------------------------------------------- #
# Request construction
# --------------------------------------------------------------------------- #

def _build_headers(
    api_key: str,
    *,
    session_id: str | None,
    use_response_cache: bool,
    cache_ttl_seconds: int | None,
    force_refresh: bool,
    app_name: str | None,
    site_url: str | None,
    extra_headers: dict[str, str] | None,
) -> dict[str, str]:
    """Assemble the HTTP headers for a single chat completion request."""
    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # OpenRouter uses these two for its leaderboard/rate-tiering; optional but
    # recommended by OpenRouter for production traffic.
    if app_name:
        headers["X-Title"] = app_name
    if site_url:
        headers["HTTP-Referer"] = site_url

    # Sticky routing / prompt-cache warmth per conversation.
    if session_id:
        headers["x-session-id"] = session_id

    # OpenRouter response caching (opt-in; a HIT bills $0).
    if use_response_cache or force_refresh:
        headers["X-OpenRouter-Cache"] = "true"
    if use_response_cache and cache_ttl_seconds is not None:
        headers["X-OpenRouter-Cache-TTL"] = str(int(cache_ttl_seconds))
    if force_refresh:
        # Bust any cached response for this exact request key.
        headers["X-OpenRouter-Cache-Clear"] = "true"

    if extra_headers:
        headers.update(extra_headers)
    return headers


def _build_payload(
    messages: list[dict[str, Any]],
    *,
    model: str,
    session_id: str | None,
    temperature: float | None,
    max_tokens: int | None,
    pin_provider: bool,
    provider_order: list[str] | None,
    extra_body: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the JSON body for a single chat completion request."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }

    # OpenRouter-native sticky routing field (kept out of the message list so it
    # never perturbs the prompt-cache prefix).
    if session_id:
        payload["session_id"] = session_id

    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    # Provider selection strategy (see the module docstring for the trade-offs):
    #   - pin_provider=True -> provider.only: ["DeepSeek"] (hard pin, no failover;
    #     requires the account's privacy policy to allow first-party DeepSeek).
    #   - provider_order    -> provider.order: [...] (preference with failover).
    provider: dict[str, Any] | None = None
    if pin_provider:
        provider = {"only": [_DEEPSEEK_PROVIDER]}
    elif provider_order:
        provider = {"order": list(provider_order), "allow_fallbacks": True}
    if provider:
        payload["provider"] = provider

    if extra_body:
        payload.update(extra_body)
    return payload


# --------------------------------------------------------------------------- #
# Response parsing / error raising (shared by sync + async paths)
# --------------------------------------------------------------------------- #

def _raise_for_status(resp: httpx.Response) -> None:
    """Raise ``OpenRouterError`` for any non-2xx response, with context."""
    if resp.status_code < 400:
        return
    request_id = resp.headers.get("x-request-id")
    try:
        data = resp.json()
        detail = data.get("error") if isinstance(data, dict) else data
    except ValueError:
        detail = resp.text[:500]
    raise OpenRouterError(
        "OpenRouter request failed with an HTTP error status",
        status_code=resp.status_code,
        body=detail,
        request_id=request_id,
    )


def _parse_response(resp: httpx.Response, body: dict[str, Any], latency_ms: float) -> dict[str, Any]:
    """Normalize a 2xx OpenRouter response into a caller-friendly dict."""
    headers = resp.headers
    usage: dict[str, Any] = body.get("usage") or {}
    prompt_details: dict[str, Any] = usage.get("prompt_tokens_details") or {}
    choice: dict[str, Any] = (body.get("choices") or [{}])[0]
    message: dict[str, Any] = choice.get("message") or {}

    cache_status = headers.get("x-openrouter-cache-status")
    cache_age = headers.get("x-openrouter-cache-age")
    cache_ttl = headers.get("x-openrouter-cache-ttl")

    return {
        # Full parsed JSON for callers that want every OpenRouter field.
        "body": body,
        "model": body.get("model"),
        "provider": body.get("provider"),
        # Convenience fields for the most common consumers.
        "content": message.get("content"),
        "message": message,
        "finish_reason": choice.get("finish_reason"),
        "usage": usage,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "prompt_tokens_details": prompt_details,
        # DeepSeek cache-hit info (cached vs uncached input tokens).
        "cached_tokens": prompt_details.get("cached_tokens"),
        # OpenRouter response-cache info.
        "cache_status": cache_status,
        "cache_age": cache_age,
        "cache_ttl": cache_ttl,
        "cache_hit": bool(cache_status and str(cache_status).upper() == "HIT"),
        "request_id": headers.get("x-request-id"),
        "headers": {
            "x-openrouter-cache-status": cache_status,
            "x-openrouter-cache-age": cache_age,
            "x-openrouter-cache-ttl": cache_ttl,
            "x-request-id": headers.get("x-request-id"),
        },
        "latency_ms": round(latency_ms, 1),
    }


def _retry_after_seconds(resp: httpx.Response) -> int | None:
    """Honor the ``Retry-After`` header if OpenRouter/DeepSeek sends one."""
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0, min(int(value), 60))
    except ValueError:
        return None


def _backoff_sleep(attempt: int, retry_after: int | None = None) -> None:
    """Sleep with exponential backoff (+ jitter) before the next attempt."""
    if retry_after is not None:
        time.sleep(retry_after)
        return
    delay = min(1.0 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.5), 10.0)
    time.sleep(delay)


# --------------------------------------------------------------------------- #
# Sync entry point
# --------------------------------------------------------------------------- #

def call_v4_flash(
    messages: list[dict],
    session_id: str | None = None,
    use_response_cache: bool = False,
    cache_ttl_seconds: int | None = None,
    force_refresh: bool = False,
    *,
    endpoint: str = "openrouter",
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    temperature: float | None = None,
    max_tokens: int | None = None,
    pin_provider: bool = False,
    provider_order: list[str] | None = None,
    app_name: str | None = None,
    site_url: str | None = None,
    extra_headers: dict[str, str] | None = None,
    extra_body: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Call DeepSeek V4 Flash and return a normalized response.

    Args:
        messages: OpenAI-compatible chat messages (``[{"role", "content"}, ...]``).
            Keep a large, byte-identical prefix (system prompt + project context)
            at the front so DeepSeek's provider-side prompt cache stays warm; only
            the tail (the user's latest turn / history) should vary.
        endpoint: ``"openrouter"`` (default, uses ``OPENROUTER_API_KEY``) or
            ``"deepseek"`` (direct first-party API, cheapest for cache-heavy
            workloads, uses ``DEEPSEEK_API_KEY``). See the module docstring for
            the cost comparison.
        session_id: Stable id for a conversation (OpenRouter only). Sent both as
            the top-level ``session_id`` body field and as the ``x-session-id``
            header, which activates OpenRouter's sticky routing: the conversation
            is pinned to whichever provider serves the first successful request,
            keeping DeepSeek's KV prompt cache warm across turns.
        use_response_cache: When True, send ``X-OpenRouter-Cache: true``
            (OpenRouter only). Only affects requests that are byte-for-byte
            identical; HITs bill $0.
        cache_ttl_seconds: When set together with ``use_response_cache``, sent as
            ``X-OpenRouter-Cache-TTL`` to control how long responses are cached.
        force_refresh: When True, send ``X-OpenRouter-Cache-Clear: true`` to bust
            any cached response for this request key (OpenRouter only).
        api_key: Override for the endpoint's environment variable.
        model: Model slug. Defaults to the endpoint's canonical slug
            (``deepseek/deepseek-v4-flash`` on OpenRouter, ``deepseek-v4-flash``
            on the direct API).
        timeout: Read/write timeout in seconds.
        max_retries: Total HTTP attempts on transient failures (429/5xx/network).
        temperature: Optional sampling temperature (None = provider default).
        max_tokens: Optional output token cap (None = provider default).
        pin_provider: When True, restrict routing to the DeepSeek provider
            (OpenRouter only). Off by default: accounts whose privacy/guardrail
            settings exclude the first-party DeepSeek endpoint would otherwise
            get ``404 No endpoints available``. Enable it only after allowing
            DeepSeek in ``https://openrouter.ai/settings/privacy``.
        provider_order: Optional ordered list of provider names to prefer, sent
            as ``provider: {"order": [...], "allow_fallbacks": true}`` (OpenRouter
            only). Softer than ``pin_provider``: first-party DeepSeek is preferred,
            but requests still fall back to any policy-allowed provider.
        app_name / site_url: Optional ``X-Title`` / ``HTTP-Referer`` for OpenRouter.
        extra_headers / extra_body: Raw overrides merged onto the request.
        client: Reusable ``httpx.Client`` (e.g. a pooled instance owned by the
            caller). If None, a short-lived client is created and closed here.

    Returns:
        A normalized dict (see ``_parse_response``): ``content``, ``message``,
        ``usage``, ``prompt_tokens_details`` / ``cached_tokens``, the OpenRouter
        cache headers (``cache_status`` / ``cache_age`` / ``cache_ttl`` /
        ``cache_hit``), ``request_id``, ``latency_ms``, and the raw ``body``.

    Raises:
        OpenRouterError: on missing API key, network failure, non-2xx status,
            or a malformed/error response body.
    """
    try:
        cfg = ENDPOINTS[endpoint]
    except KeyError:
        raise OpenRouterError(f"Unknown endpoint {endpoint!r}; choose from {sorted(ENDPOINTS)}")

    url = f"{cfg['base_url']}/chat/completions"
    model = model or cfg["model"]
    key_env = cfg["key_env"]

    key = api_key or os.environ.get(key_env)
    if not key:
        raise OpenRouterError(f"{key_env} is not set in the environment")

    # OpenRouter-only features: response caching, session_id sticky routing, and
    # provider selection. The direct DeepSeek API is stateless and
    # single-provider, so it relies on automatic prefix caching only.
    is_openrouter = endpoint == "openrouter"
    effective_session = session_id if is_openrouter else None
    effective_cache = use_response_cache if is_openrouter else False
    effective_ttl = cache_ttl_seconds if is_openrouter else None
    effective_refresh = force_refresh if is_openrouter else False
    effective_pin = pin_provider if is_openrouter else False
    effective_order = provider_order if is_openrouter else None

    headers = _build_headers(
        key,
        session_id=effective_session,
        use_response_cache=effective_cache,
        cache_ttl_seconds=effective_ttl,
        force_refresh=effective_refresh,
        app_name=app_name,
        site_url=site_url,
        extra_headers=extra_headers,
    )
    payload = _build_payload(
        messages,
        model=model,
        session_id=effective_session,
        temperature=temperature,
        max_tokens=max_tokens,
        pin_provider=effective_pin,
        provider_order=effective_order,
        extra_body=extra_body,
    )

    owns_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(connect=15.0, read=timeout, write=timeout, pool=15.0),
    )

    attempt = 0
    try:
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                resp = client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                if attempt >= max_retries:
                    raise OpenRouterError(
                        "OpenRouter network/transport error",
                        cause=exc,
                    ) from exc
                log.warning("openrouter transport error (attempt %d/%d): %s", attempt, max_retries, exc)
                _backoff_sleep(attempt)
                continue

            latency_ms = (time.monotonic() - started) * 1000.0

            if resp.status_code >= 400:
                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                    log.warning(
                        "openrouter transient HTTP %d (attempt %d/%d)",
                        resp.status_code, attempt, max_retries,
                    )
                    _backoff_sleep(attempt, _retry_after_seconds(resp))
                    continue
                _raise_for_status(resp)  # always raises for 4xx/5xx

            try:
                body = resp.json()
            except ValueError:
                raise OpenRouterError(
                    "OpenRouter returned a non-JSON body",
                    status_code=resp.status_code,
                    body=resp.text[:500],
                    request_id=resp.headers.get("x-request-id"),
                ) from None

            # OpenRouter occasionally returns HTTP 200 with an error object.
            if isinstance(body, dict) and body.get("error"):
                raise OpenRouterError(
                    "OpenRouter returned an error object in the response body",
                    status_code=resp.status_code,
                    body=body.get("error"),
                    request_id=resp.headers.get("x-request-id"),
                )

            return _parse_response(resp, body, latency_ms)
    finally:
        if owns_client:
            client.close()


# --------------------------------------------------------------------------- #
# Async entry point (for asyncio-based orchestrators / swarm runtimes)
# --------------------------------------------------------------------------- #

async def acall_v4_flash(
    messages: list[dict],
    session_id: str | None = None,
    use_response_cache: bool = False,
    cache_ttl_seconds: int | None = None,
    force_refresh: bool = False,
    *,
    endpoint: str = "openrouter",
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    temperature: float | None = None,
    max_tokens: int | None = None,
    pin_provider: bool = False,
    provider_order: list[str] | None = None,
    app_name: str | None = None,
    site_url: str | None = None,
    extra_headers: dict[str, str] | None = None,
    extra_body: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Async twin of :func:`call_v4_flash` — same semantics, ``httpx.AsyncClient``.

    Prefer this over ``asyncio.to_thread(call_v4_flash, ...)`` inside a swarm:
    it does not consume a thread per request and can share one pooled
    ``httpx.AsyncClient`` across the whole orchestration layer.
    """
    try:
        cfg = ENDPOINTS[endpoint]
    except KeyError:
        raise OpenRouterError(f"Unknown endpoint {endpoint!r}; choose from {sorted(ENDPOINTS)}")

    url = f"{cfg['base_url']}/chat/completions"
    model = model or cfg["model"]
    key_env = cfg["key_env"]

    key = api_key or os.environ.get(key_env)
    if not key:
        raise OpenRouterError(f"{key_env} is not set in the environment")

    is_openrouter = endpoint == "openrouter"
    effective_session = session_id if is_openrouter else None
    effective_cache = use_response_cache if is_openrouter else False
    effective_ttl = cache_ttl_seconds if is_openrouter else None
    effective_refresh = force_refresh if is_openrouter else False
    effective_pin = pin_provider if is_openrouter else False
    effective_order = provider_order if is_openrouter else None

    headers = _build_headers(
        key,
        session_id=effective_session,
        use_response_cache=effective_cache,
        cache_ttl_seconds=effective_ttl,
        force_refresh=effective_refresh,
        app_name=app_name,
        site_url=site_url,
        extra_headers=extra_headers,
    )
    payload = _build_payload(
        messages,
        model=model,
        session_id=effective_session,
        temperature=temperature,
        max_tokens=max_tokens,
        pin_provider=effective_pin,
        provider_order=effective_order,
        extra_body=extra_body,
    )

    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(connect=15.0, read=timeout, write=timeout, pool=15.0),
    )

    attempt = 0
    try:
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                if attempt >= max_retries:
                    raise OpenRouterError(
                        "OpenRouter network/transport error",
                        cause=exc,
                    ) from exc
                log.warning("openrouter transport error (attempt %d/%d): %s", attempt, max_retries, exc)
                await _abackoff_sleep(attempt)
                continue

            latency_ms = (time.monotonic() - started) * 1000.0

            if resp.status_code >= 400:
                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                    log.warning(
                        "openrouter transient HTTP %d (attempt %d/%d)",
                        resp.status_code, attempt, max_retries,
                    )
                    await _abackoff_sleep(attempt, _retry_after_seconds(resp))
                    continue
                _raise_for_status(resp)

            try:
                body = resp.json()
            except ValueError:
                raise OpenRouterError(
                    "OpenRouter returned a non-JSON body",
                    status_code=resp.status_code,
                    body=resp.text[:500],
                    request_id=resp.headers.get("x-request-id"),
                ) from None

            if isinstance(body, dict) and body.get("error"):
                raise OpenRouterError(
                    "OpenRouter returned an error object in the response body",
                    status_code=resp.status_code,
                    body=body.get("error"),
                    request_id=resp.headers.get("x-request-id"),
                )

            return _parse_response(resp, body, latency_ms)
    finally:
        if owns_client:
            await client.aclose()


async def _abackoff_sleep(attempt: int, retry_after: int | None = None) -> None:
    """Async variant of ``_backoff_sleep``."""
    import asyncio

    if retry_after is not None:
        await asyncio.sleep(retry_after)
        return
    delay = min(1.0 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.5), 10.0)
    await asyncio.sleep(delay)


# --------------------------------------------------------------------------- #
# Prompt-caching helper
# --------------------------------------------------------------------------- #

def build_messages(
    system_prompt: str,
    shared_context: str,
    user_content: str,
) -> list[dict]:
    """Build a chat message list optimized for DeepSeek provider-side prompt caching.

    The returned list is exactly::

        [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": shared_context},
            {"role": "user",   "content": user_content},
        ]

    The two leading entries are your *stable prefix*. As long as
    ``system_prompt`` and ``shared_context`` never change, the serialized JSON
    prefix of every request built through this helper is byte-for-byte identical,
    so DeepSeek reuses the cached KV state for the entire prefix on every call
    and only pays full price for the trailing ``user_content``.

    Why a big stable prefix matters:
        * DeepSeek's context cache only engages once the prefix is large enough
          (roughly >= 1000 tokens). Below that, requests are too short to bother
          caching and you pay the full uncached input rate every time.
        * Put the system prompt, tool schemas, and project/agent context here.
          Never interpolate per-request values (timestamps, random ids, session
          state) into the prefix -- any change invalidates the whole prefix.

    Multi-turn conversations:
        Keep the prefix first and *append* prior turns, then the latest user
        message, e.g.::

            msgs = build_messages(system, context, first_question)
            msgs += [{"role": "assistant", "content": a1},
                     {"role": "user", "content": second_question}]

        Append-only. Never reorder or rewrite earlier turns, or the prefix
        changes and the cache misses.
    """
    return [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": shared_context},
        {"role": "user", "content": user_content},
    ]


# --------------------------------------------------------------------------- #
# Instrumentation / logging
# --------------------------------------------------------------------------- #

def log_usage(
    response: dict[str, Any],
    *,
    session_id: str | None = None,
    logger: logging.Logger | None = None,
    level: int = logging.INFO,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit a structured, single-line log record for one completed call.

    Records total prompt/completion tokens, ``cached_tokens`` (DeepSeek prompt
    cache hits), and the OpenRouter response-cache headers. Never uses print.

    Args:
        response: The dict returned by :func:`call_v4_flash` / :func:`acall_v4_flash`.
        session_id: Conversation id to include in the record.
        logger: Target logger (defaults to this module's logger).
        level: Logging level (default INFO).
        extra: Additional structured fields to merge into the record.

    Returns:
        The structured fields dict, for callers that want to forward it to
        structlog / JSON logging handlers.
    """
    target = logger or log
    fields: dict[str, Any] = {
        "event": "deepseek.v4_flash.completed",
        "session_id": session_id,
        "model": response.get("model"),
        "provider": response.get("provider"),
        "cache_status": response.get("cache_status"),
        "cache_age": response.get("cache_age"),
        "cache_ttl": response.get("cache_ttl"),
        "cache_hit": response.get("cache_hit"),
        "prompt_tokens": response.get("prompt_tokens"),
        "cached_tokens": response.get("cached_tokens"),
        "completion_tokens": response.get("completion_tokens"),
        "total_tokens": response.get("total_tokens"),
        "request_id": response.get("request_id"),
        "latency_ms": response.get("latency_ms"),
    }
    if extra:
        fields.update(extra)
    target.log(level, "deepseek.v4_flash.completed %s", json.dumps(fields, sort_keys=True))
    return fields


# --------------------------------------------------------------------------- #
# Runnable example (demo only -- real integrations import the module instead)
# --------------------------------------------------------------------------- #

def _demo_system_prompt() -> str:
    return """\
You are the orchestration brain of a production multi-agent coding swarm.

## Agent roles you coordinate
- coordinator: high-level routing and synthesis; decides which specialist runs next.
- planner: deep reasoning, task decomposition, and system design.
- researcher: information gathering, codebase exploration, and web research.
- coder: writes and refactors production code, always reading before writing.
- reviewer: pedantic code review and bug finding; blocks anything unverified.
- debugger: deep logic and error-trace analysis for the hardest failures.

## Operating principles
1. Read before you write. Never guess a file path or an API signature: list the
   directory, read the module, and only then propose an edit.
2. Verify after every change. A change is not done until the relevant tests and
   a typecheck have actually run and passed. Never report SUCCESS on untested code.
3. Keep a working todo list. Maintain it across turns so work survives context
   compaction; mark items done only when the evidence exists.
4. Prefer small, focused diffs over large rewrites. Preserve existing behavior
   unless a task explicitly asks to change it.
5. When in doubt about intent, ask for clarification instead of guessing.

## Tool-use decision format
You emit structured JSON decisions of the form:
{"thinking": "...", "action": "tool_name", "params": {...}, "notes": "..."}
Tool calls must be valid JSON on the first attempt. If a tool fails, read the
error, correct the parameters, and retry with a bounded number of attempts
before escalating to the debugger agent.

## Context budget
The shared project context below is deliberately large and stable so the
provider-side prompt cache stays warm. Treat it as ground truth for module
layout and conventions; never invent paths that contradict it.
"""


def _demo_shared_context() -> str:
    return """\
## Project map (v-horseshoe-v2)
Four-layer architecture:
- swarm_os/  -- core swarm platform: orchestrator, HTTP API, healing, memory bridge,
                control plane, RV-finder package, repositories (data access layer).
- runtime_v2/ -- async agent runtime: agent loop, LLM client, tool execution,
                stream runner, fallback manager, memory core (Qdrant-backed).
- src/       -- next-gen agent runtime & memory stores (agent_runtime, orchestrator,
                hybrid memory, policy graph, circuit breaker).
- organism_console/ -- CLI interactive shell frontend and watchdog daemons.
- start-console/ and organism-console/ -- React/TanStack web consoles (frontend only;
                generation speed and correctness live in llama.cpp and the Python runtime).

## Key runtime facts
- Test framework: pytest (pytest.ini at root). Python >= 3.14 in production; keep code
  backward-compatible to 3.11 for library modules.
- Build: setuptools, `organism` CLI entrypoint. The npm consoles are thin clients.
- Default local chat model: qwen3.5-4b on port 8080. Heavy reasoning and analysis
  agents route to cloud DeepSeek V4 Flash via OpenRouter whenever a key is present.
- Memory: Qdrant vector store (async client, port 6333), embeddings via nomic-embed
  (port 8081), reranker on port 8082, dedicated 0.8B summarizer on port 8084.

## Engineering conventions
- All new services are asyncio-native: asyncio.timeout(), AsyncQdrantClient, asyncio.Lock,
  asyncio.Queue. Never call asyncio.wait_for (deprecated).
- Use httpx with module-level pooled clients (max_keepalive_connections=5, max_connections=20);
  never create a per-request client.
- Prefer asyncio.timeout() context managers over wait_for; wrap fire-and-forget tasks
  with strong references and done-callbacks.
- Repository pattern for data access (event_log_repo, graph_repo, mutation_repo, snapshots).
- Type hints everywhere; no comments unless they explain a non-obvious decision.
- Log via the logging module with structured fields; never bare except:, never print().

## Cloud model policy
- Free models and DeepSeek V4 Flash only. Claude/Anthropic and gpt-4 are hard-blocked.
- When a cloud call fails, fall back through the local chain (qwen3.5-4b last) rather
  than failing the whole run. Respect cooldowns and the circuit breaker (3 strikes).
- Keep system prompts and tool schemas frozen across turns to preserve prompt caching.
"""


def _demo_user_question() -> str:
    return (
        "Refactor the tool executor so the ExternalMCPClientManager singleton is "
        "safe under concurrent first calls (an asyncio.Lock guard), keeping the "
        "existing dispatch contract and adding a regression test."
    )


if __name__ == "__main__":
    # --- The example below demonstrates BOTH caching layers. ---
    #
    # Response caching (X-OpenRouter-Cache) only helps when the request is
    # byte-for-byte identical. Here we call with the exact same messages twice:
    # the 1st call is a MISS (billed normally, DeepSeek prompt cache may warm),
    # the 2nd call is an OpenRouter HIT (billed $0). That pattern is right for
    # FAQs, deterministic test harnesses, and retries.
    #
    # For a live, changing conversation you should instead rely on PROMPT
    # caching only: keep the big stable prefix from build_messages() and let the
    # per-turn tail vary. Do NOT enable response caching there -- every turn
    # differs, so you'd pay cache overhead for zero HITs.

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    # Cheapest-to-run config: endpoint="deepseek" (direct first-party API) once a
    # DEEPSEEK_API_KEY exists -- DeepSeek's automatic context caching bills cached
    # input at ~$0.0028/M (98% cheaper than a miss), which dominates the cost for
    # a swarm with a large frozen prefix. Until then, endpoint="openrouter"
    # (default) uses your existing OPENROUTER_API_KEY credits. Select via
    # V4_FLASH_ENDPOINT=deepseek to switch.
    endpoint = os.environ.get("V4_FLASH_ENDPOINT", "openrouter")
    key_env = "DEEPSEEK_API_KEY" if endpoint == "deepseek" else "OPENROUTER_API_KEY"
    api_key = os.environ.get(key_env)
    if not api_key:
        raise SystemExit(
            f"{key_env} is not set. Set it in the environment (or .env) before "
            f"running this example (endpoint={endpoint})."
        )

    session = "demo-session-0001"

    messages = build_messages(
        system_prompt=_demo_system_prompt(),
        shared_context=_demo_shared_context(),
        user_content=_demo_user_question(),
    )

    # Shared options: stable session for sticky routing/prompt-cache warmth,
    # response caching enabled for 1 hour, and DeepSeek preferred for routing.
    # Use pin_provider=True here instead of provider_order only after allowing
    # the DeepSeek provider at https://openrouter.ai/settings/privacy (a hard pin
    # 404s on accounts whose data policy excludes the first-party endpoint).
    common = dict(
        endpoint=endpoint,
        session_id=session,
        use_response_cache=True,
        cache_ttl_seconds=3600,
        provider_order=["DeepSeek"],
        app_name="v-horseshoe-demo",
    )

    print("Call 1/2 (expected: cache MISS, provider bills normally)...")
    r1 = call_v4_flash(messages, **common)
    log_usage(r1, session_id=session)

    print("Call 2/2 (expected: OpenRouter cache HIT, billed $0)...")
    r2 = call_v4_flash(messages, **common)
    log_usage(r2, session_id=session)

    print("\nForcing a cache clear (expected: cache MISS again, fresh generation)...")
    r3 = call_v4_flash(messages, **common, force_refresh=True)
    log_usage(r3, session_id=session)

    print(f"\n=== demo results ===")
    print(f"call 1: status={r1['cache_status']!r:6} cached_input_tokens={r1['cached_tokens']} latency={r1['latency_ms']}ms")
    print(f"call 2: status={r2['cache_status']!r:6} cached_input_tokens={r2['cached_tokens']} latency={r2['latency_ms']}ms")
    print(f"call 3: status={r3['cache_status']!r:6} cached_input_tokens={r3['cached_tokens']} latency={r3['latency_ms']}ms")
    print(f"content (call 1): {str(r1['content'])[:120]!r}...")
