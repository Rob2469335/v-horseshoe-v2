"""LLM client configuration and API call wrappers for tool decisions."""
import os
import re
import ssl
import logging
import asyncio
from typing import AsyncGenerator

import litellm
from runtime_v2.services.model_registry import get_model
from runtime_v2.services._grammar_schema import TOOL_DECISION_JSON_SCHEMA

log = logging.getLogger(__name__)

# TASK 1 (grammar-constrained local decoding): cache a flag + a startup log so
# the "[grammar-decode] enabled" line is emitted once at first use, not per call.
_grammar_decoded_logged = False


def _grammar_decode_enabled() -> bool:
    """Constrain the LOCAL llama.cpp path to emit schema-valid tool-decision JSON
    only when SWARM_GRAMMAR_DECODE=1. Cloud/DeepSeek path is never constrained."""
    return os.environ.get("SWARM_GRAMMAR_DECODE") == "1"


def _grammar_response_format() -> dict:
    global _grammar_decoded_logged
    if not _grammar_decoded_logged:
        log.info("[grammar-decode] enabled for local tool-decision calls")
        _grammar_decoded_logged = True
    return {
        "type": "json_schema",
        "json_schema": {"schema": TOOL_DECISION_JSON_SCHEMA, "strict": True},
    }


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


# Heavy analysis/research agents prefer a fast cloud model when available.
# Default DeepSeek V4 Flash (sanctioned cheap model); override via
# ANALYSIS_CLOUD_MODEL. Disable with SWARM_ANALYSIS_CLOUD=off or by running
# /local (SWARM_ROUTING_MODE=local_only). Agent keys mirror _agent_config.
_ANALYSIS_CLOUD_AGENTS = ("code_analyzer", "reviewer", "researcher")


def _analysis_cloud_model() -> str:
    return os.getenv("ANALYSIS_CLOUD_MODEL", "openrouter/deepseek/deepseek-chat")


def _analysis_cloud_enabled() -> bool:
    mode = os.getenv("SWARM_ROUTING_MODE", "").strip().lower()
    if mode == "local_only":
        return False
    flag = os.getenv("SWARM_ANALYSIS_CLOUD", "auto").strip().lower()
    if flag in ("off", "0", "false", "local"):
        return False
    return bool(os.getenv("OPENROUTER_API_KEY"))


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

    # UPGRADE: route heavy analysis/research work to a fast cloud model when a
    # key is present and cloud is enabled. A 9B local model at ~6 t/s makes
    # codebase audits and web-research synthesis take tens of seconds per step.
    # IMPORTANT: skip the cloud hop if the cloud model is already in a cooldown
    # window (billing/auth failure recorded via record_model_failure) — otherwise
    # every decision re-selects the same doomed provider and burns the retry budget.
    if agent_id in _ANALYSIS_CLOUD_AGENTS and _analysis_cloud_enabled():
        # UPGRADE (Item #8): win-rate-gated online routing — keep the cloud hop
        # only while the tracked success rate is healthy; repeated failures decay
        # back to local. Defaults to the legacy behavior unless enabled.
        try:
            from runtime_v2.services.online_routing import cloud_allowed_for_agent
            if not cloud_allowed_for_agent(agent_id):
                log.info("[routing] %s: win-rate gated analysis off cloud → local", agent_id)
                cloud_model = None
            else:
                cloud_model = _analysis_cloud_model()
        except Exception:
            cloud_model = _analysis_cloud_model()
        if cloud_model is not None:
            try:
                from runtime_v2.services.fallback_manager import is_model_cooled_down
                if is_model_cooled_down(cloud_model):
                    log.info("[routing] %s -> cloud analysis model %s in cooldown, falling back to local", agent_id, cloud_model)
                    cloud_model = None
            except Exception:
                pass
        if cloud_model is not None:
            log.info("[routing] %s -> cloud analysis model %s", agent_id, cloud_model)
            return cloud_model

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
    cloud_max_tokens = int(os.getenv("CLOUD_MAX_TOKENS", "4096"))

    async def _call(extra: dict, max_tokens: int):
        extra["max_tokens"] = max_tokens
        kwargs = build_kwargs(litellm_model, extra, fallbacks)
        kwargs["max_retries"] = 0
        kwargs["timeout"] = 300.0
        return await litellm.acompletion(**kwargs)

    extra = {
        "messages": messages,
        "temperature": 0.7 if is_cloud else 0.2,
        "top_p": 0.95 if is_cloud else 0.9,
        "frequency_penalty": 0.1 if not is_cloud else 0.0,
        "presence_penalty": 0.1 if not is_cloud else 0.0,
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
        # TASK 1 (opt-in grammar decode): constrain the local tool-decision path
        # to schema-valid JSON only. Cloud/DeepSeek never receives response_format.
        if _grammar_decode_enabled():
            extra["response_format"] = _grammar_response_format()
        return await _call(extra, local_max_tokens)
    extra["response_format"] = {"type": "json_object"}
    try:
        return await _call(extra, cloud_max_tokens)
    except Exception as exc:
        # UPGRADE: OpenRouter returns HTTP 402 with "can only afford N tokens"
        # when the account balance is too low for the requested max_tokens. This
        # is a soft limit, not a hard failure — re-issue with the affordable cap
        # (minus a small safety margin) instead of surfacing the 402 to the
        # agent's retry loop (which would burn the budget on a permanent-looking
        # but actually self-healing condition).
        match = re.search(r"can only afford (\d+)", str(exc))
        if is_cloud and match:
            affordable = int(match.group(1))
            clamped = max(128, affordable - 128)
            log.warning(
                "Cloud model %s capped at %d tokens (balance-limited) — retrying with max_tokens=%d",
                litellm_model, affordable, clamped,
            )
            return await _call(extra, clamped)
        raise


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
