"""LLM client configuration and API call wrappers for tool decisions."""
import os
import ssl
import logging
import asyncio
from typing import AsyncGenerator

import litellm
from runtime_v2.services.model_registry import get_model

log = logging.getLogger(__name__)


def bootstrap_ssl():
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
        ssl.create_default_context = ssl._create_unverified_context
    except AttributeError:
        pass
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
        os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    except ImportError:
        pass


def get_routing_mode() -> str:
    mode = os.getenv("SWARM_ROUTING_MODE", "local_only").strip().lower()
    if mode not in ("auto", "local_only", "cloud_allowed"):
        return "local_only"
    return mode


def get_litellm_model(agent_id: str, fallback_model: str) -> str:
    default_model, backend = get_model(agent_id)
    model = fallback_model if fallback_model else default_model

    # SAFEGUARD: Never allow expensive Claude / Anthropic / Sonnet / Opus models
    if any(forbidden in model.lower() for forbidden in ("claude", "anthropic", "sonnet", "opus", "gpt-4")):
        log.warning(f"Intercepted forbidden expensive model '{model}' -> enforcing DeepSeek V4 Flash ('openrouter/deepseek/deepseek-chat')")
        return "openrouter/deepseek/deepseek-chat"

    if model.startswith("router/"):
        model = model.split("/", 1)[1]

    if model.startswith("openai/") or model.startswith("llama/"):
        model_name = model.split("/", 1)[1]
        return f"openai/{model_name}"

    if "/" in model and not model.startswith("llama") and backend not in ("llama", "local"):
        return model

    if backend in ("llama", "local", "router") or model.startswith("llama/"):
        model_name = model.replace("llama/", "") if model.startswith("llama/") else model
        return f"openai/{model_name}"

    if backend == "openrouter":
        return f"openrouter/{model}"
    if backend == "groq":
        return f"groq/{model}"
    if backend == "nvidia":
        return f"nvidia_nim/{model}"
    if backend == "gemini":
        return f"gemini/{model}"
    return f"{backend}/{model}" if "/" not in model else model


def build_kwargs(litellm_model: str, extra: dict, fallbacks: list) -> dict:
    kwargs = {"model": litellm_model, "fallbacks": fallbacks, "timeout": 600.0, **extra}
    if litellm_model.startswith("openai/"):
        model_name = litellm_model.replace("openai/", "").lower()
        if "moondream" in model_name or "vl" in model_name:
            kwargs["api_base"] = "http://127.0.0.1:8083/v1"
        else:
            kwargs["api_base"] = "http://127.0.0.1:8080/v1"
        kwargs["api_key"] = "llama"
    return kwargs


def inject_system_prompt(messages: list, system: str) -> list:
    messages = list(messages)
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            messages[i] = {"role": "system", "content": system}
            return messages
    return [{"role": "system", "content": system}] + messages


async def complete_for_tool_decision(litellm_model: str, messages: list, fallbacks: list):
    is_cloud = not litellm_model.startswith("openai/")
    # Local tool decisions need enough room for thought + JSON action + params.
    # 250 tokens caused truncated JSON (missing closing braces/fields) → retry loops.
    # Match the model's actual context: 16384 / 4 = 4096 is a safe per-request cap.
    local_max_tokens = 4096
    extra = {
        "messages": messages,
        "temperature": 0.7 if is_cloud else 0.2,
        "top_p": 0.95 if is_cloud else 0.9,
        "frequency_penalty": 0.1 if not is_cloud else 0.0,
        "presence_penalty": 0.1 if not is_cloud else 0.0,
        "max_tokens": 4096 if is_cloud else local_max_tokens,
        "num_ctx": 16384,
    }
    if not is_cloud:
        extra["extra_body"] = {
            "max_tokens": local_max_tokens,
            "n_predict": local_max_tokens,
            "repeat_penalty": 1.15,
            "repeat_last_n": 256,
            "cache_prompt": False,
            "id_slot": -1,
            "frequency_penalty": 0.1,
            "presence_penalty": 0.1,
        }
    if is_cloud:
        extra["response_format"] = {"type": "json_object"}

    kwargs = build_kwargs(litellm_model, extra, fallbacks)
    kwargs["max_retries"] = 0
    kwargs["timeout"] = 300.0
    return await litellm.acompletion(**kwargs)


async def stream_content(model: str, messages: list, agent_id: str) -> AsyncGenerator[tuple[str, str], None]:
    litellm_model = get_litellm_model(agent_id, model)
    routing_mode = get_routing_mode()
    from runtime_v2.services.fallback_manager import get_live_fallbacks
    raw_fallbacks = await get_live_fallbacks(mode=routing_mode)
    fallbacks = [f["model"] for f in raw_fallbacks if not f["model"].startswith("openai/")][:3]

    extra = {
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "repetition_penalty": 1.05,
        "max_tokens": 32768,
        "num_ctx": 32768,
    }
    kwargs = build_kwargs(litellm_model, extra, fallbacks)
    kwargs["timeout"] = 900.0

    try:
        kwargs["max_retries"] = 0
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            if not chunk.choices:
                continue
            piece = chunk.choices[0].delta.content or ""
            if piece:
                yield piece, "content"
    except Exception as exc:
        log.error("[%s] stream error: %s", agent_id, exc)
        yield str(exc), "error"
