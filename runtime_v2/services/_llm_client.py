"""LLM client configuration and API call wrappers for tool decisions."""

import os
import re
import ssl
import logging
import threading
from typing import AsyncGenerator

import litellm
from runtime_v2.services.fallback_manager import _is_local_model
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


def _cloud_response_format(litellm_model: str) -> dict:
    """P2: structured outputs for the CLOUD tool-decision path. Use strict
    json_schema (schema-constrained JSON) when the provider supports it — this
    prevents the malformed-JSON retry loops that the regex-salvage parser used to
    absorb. Fall back to plain json_object for providers without json_schema
    support (e.g. some llama.cpp forks / older endpoints).

    LIVE-VERIFIED 2026-08-06: the OpenCode Go/Zen proxies
    (https://opencode.ai/zen/go/v1) REJECT the strict json_schema response_format
    with `400 invalid_request_error: This response_format type is unavailable
    now` — even though litellm's supports_response_schema("openai/deepseek-v4-
    flash") returns True (it trusts the DeepSeek provider table, not the actual
    proxy). Because the analysis-cloud primary IS openai/deepseek-v4-flash, every
    cloud tool decision died on that 400 (non-retryable → the Router never reached
    the fallback chain) and the /upgrade loop failed with "Unable to determine
    next action". json_object is verified 200 on the same endpoint. So: any model
    whose RESOLVED endpoint is an OpenCode proxy gets json_object, regardless of
    what litellm's support table says."""
    try:
        base, _, _ = _endpoint_for(litellm_model)
        if base and "opencode.ai" in base:
            return {"type": "json_object"}
        from litellm import supports_response_schema

        if supports_response_schema(litellm_model):
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "tool_decision",
                    "schema": TOOL_DECISION_JSON_SCHEMA,
                    "strict": True,
                },
            }
    except Exception as exc:
        log.debug("[_cloud_response_format] supports_response_schema raised: %s", exc)
    return {"type": "json_object"}


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


# Heavy analysis/research/edit agents prefer a fast cloud model when available.
# Default DeepSeek V4 Flash served via the funded OpenCode Go account
# (openai/... routes to https://opencode.ai/zen/go/v1 in build_kwargs); override
# via ANALYSIS_CLOUD_MODEL. Disable with SWARM_ANALYSIS_CLOUD=off or by running
# /local (SWARM_ROUTING_MODE=local_only). Agent keys mirror _agent_config.
# coder/debugger are included because the edit protocol (read -> patch ->
# sandbox_repl verify -> final) needs strong instruction-following; the local
# 4B model reproduces the /upgrade dead-loop (web_search then final, no edit).
# executor is included because it now orchestrates compound goals (chaining
# researcher -> coder -> tool-runner); the local 4B cannot follow a multi-agent
# chain reliably.
_ANALYSIS_CLOUD_AGENTS = (
    "code_analyzer",
    "reviewer",
    "researcher",
    "coder",
    "debugger",
    "executor",
)


def _analysis_cloud_model() -> str:
    return os.getenv(
        "ANALYSIS_CLOUD_MODEL", "nvidia_nim/deepseek-ai/deepseek-v4-flash-0731"
    )


def _analysis_cloud_enabled() -> bool:
    mode = os.getenv("SWARM_ROUTING_MODE", "").strip().lower()
    if mode == "local_only":
        return False
    flag = os.getenv("SWARM_ANALYSIS_CLOUD", "auto").strip().lower()
    if flag in ("off", "0", "false", "local"):
        return False
    # Free providers only: a no-cost key (NVIDIA/Groq/Gemini/OpenRouter) enables
    # the analysis-cloud hop. Paid-only accounts (OPENAI_API_KEY = OpenCode Go,
    # DEEPSEEK_API_KEY = DeepSeek direct) must NOT satisfy it — free credit burns
    # first; the paid accounts stay last-resort fallbacks, not the default.
    return any(
        os.getenv(k)
        for k in (
            "NVIDIA_API_KEY",
            "GROQ_API_KEY",
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "DEEPSEEK_API_KEY",
            "OPENROUTER_API_KEY",
        )
    )


def get_routing_mode() -> str:
    mode = os.getenv("SWARM_ROUTING_MODE", "auto").strip().lower()
    if mode not in ("auto", "local_only", "cloud_allowed"):
        return "local_only"
    return mode


def get_litellm_model(
    agent_id: str, fallback_model: str, force_local: bool = False
) -> str:
    default_model, backend = get_model(agent_id)
    model = fallback_model if fallback_model else default_model

    # SAFEGUARD: Never allow expensive Claude / Anthropic / Sonnet / Opus /
    # OpenAI o-series / GPT-4 class models — including o1/o3 (line 204 routes
    # them to the paid OpenCode Go tier; this guard must intercept them too).
    if any(
        forbidden in model.lower()
        for forbidden in ("claude", "anthropic", "sonnet", "opus", "gpt-4", "o1", "o3")
    ):
        log.warning(
            f"Intercepted forbidden expensive model '{model}' -> enforcing DeepSeek V4 Flash ('{_analysis_cloud_model()}')"
        )
        return _analysis_cloud_model()

    # UPGRADE: route heavy analysis/research work to a fast cloud model when a
    # key is present and cloud is enabled. A 4B local model at ~6 t/s makes
    # codebase audits and web-research synthesis take tens of seconds per step.
    # IMPORTANT: skip the cloud hop if the cloud model is already in a cooldown
    # window (billing/auth failure recorded via record_model_failure) — otherwise
    # every decision re-selects the same doomed provider and burns the retry budget.
    # force_local=True (billing-402 degrade path) skips this branch entirely so the
    # caller can force the run onto the local llama.cpp model.
    if (
        not force_local
        and agent_id in _ANALYSIS_CLOUD_AGENTS
        and _analysis_cloud_enabled()
        and not _is_local_model(model)
    ):
        # UPGRADE (Item #8): win-rate-gated online routing — keep the cloud hop
        # only while the tracked success rate is healthy; repeated failures decay
        # back to local. Defaults to the legacy behavior unless enabled.
        try:
            from runtime_v2.services.online_routing import cloud_allowed_for_agent

            if not cloud_allowed_for_agent(agent_id):
                log.info(
                    "[routing] %s: win-rate gated analysis off cloud → local", agent_id
                )
                cloud_model = None
            else:
                cloud_model = _analysis_cloud_model()
        except Exception:
            cloud_model = _analysis_cloud_model()
        if cloud_model is not None:
            try:
                from runtime_v2.services.fallback_manager import is_model_cooled_down

                if is_model_cooled_down(cloud_model):
                    log.info(
                        "[routing] %s -> cloud analysis model %s in cooldown, falling back to local",
                        agent_id,
                        cloud_model,
                    )
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

    if (
        "/" in model
        and not model.startswith("llama")
        and backend not in ("llama", "local")
    ):
        return model

    if backend in ("llama", "local", "router") or model.startswith("llama/"):
        model_name = (
            model.replace("llama/", "") if model.startswith("llama/") else model
        )
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


def _endpoint_for(litellm_model: str) -> tuple[str, str, str]:
    """Resolve (api_base, api_key, effective_model) for a single litellm model id.

    Single source of truth for BOTH the primary model and every fallback. Each
    provider gets its OWN endpoint+key so litellm never reuses the primary's
    api_base/api_key on a different provider (which sent NVIDIA/Groq/Gemini to the
    OpenCode Go URL and leaked the OpenCode key to third parties). Native providers
    (nvidia_nim/, groq/, gemini/, openrouter/, deepseek/) return (None, None, model)
    so litellm uses its own provider config.
    """
    if litellm_model.startswith("openai/"):
        name = litellm_model.replace("openai/", "").lower()
        if "moondream" in name or "vl" in name:
            return "http://127.0.0.1:8083/v1", "llama", litellm_model
        if name.startswith("zen/"):
            # OpenCode Zen FREE tier — $0 deepseek-v4-flash etc. litellm would
            # send "zen/deepseek-v4-flash" as the model id; rewrite it to the
            # plain id the endpoint expects.
            return (
                os.getenv("OPENCODE_ZEN_BASE", "https://opencode.ai/zen/v1"),
                os.getenv("OPENAI_API_KEY", ""),
                f"openai/{name.split('/', 1)[1]}",
            )
        if (
            name.startswith("gpt")
            or name.startswith("o1")
            or name.startswith("o3")
            or "deepseek" in name
        ):
            # OpenCode Go PAID tier / OpenAI paid API — use env vars set in start-dev.ps1
            return (
                os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1"),
                os.getenv("OPENAI_API_KEY", ""),
                litellm_model,
            )
        return "http://127.0.0.1:8080/v1", "llama", litellm_model
    if litellm_model.startswith("nvidia_nim/"):
        return (
            None,
            os.getenv("NVIDIA_NIM_API_KEY") or os.getenv("NVIDIA_API_KEY", ""),
            litellm_model,
        )
    if litellm_model.startswith("groq/"):
        return None, os.getenv("GROQ_API_KEY", ""), litellm_model
    if litellm_model.startswith("gemini/"):
        return None, os.getenv("GEMINI_API_KEY", ""), litellm_model
    if litellm_model.startswith("openrouter/"):
        return None, os.getenv("OPENROUTER_API_KEY", ""), litellm_model
    if litellm_model.startswith("deepseek/"):
        return None, os.getenv("DEEPSEEK_API_KEY", ""), litellm_model
    return None, None, litellm_model


def _fallback_entry(model_id: str) -> dict:
    """Per-fallback deployment config for litellm.acompletion(fallbacks=[...])."""
    base, key, eff = _endpoint_for(model_id)
    entry = {"model": eff}
    if base:
        entry["api_base"] = base
    if key:
        entry["api_key"] = key
    from runtime_v2.services.fallback_manager import _is_local_model

    if not _is_local_model(model_id):
        # Cloud providers must not inherit llama.cpp engine-specific extra_body (id_slot, n_predict)
        entry["extra_body"] = {}
    return entry


def build_kwargs(litellm_model: str, extra: dict, fallbacks: list) -> dict:
    kwargs = {
        "model": litellm_model,
        "fallbacks": [
            _fallback_entry(f) if isinstance(f, str) else f for f in (fallbacks or [])
        ],
        "timeout": 600.0,
        **extra,
    }
    base, key, eff = _endpoint_for(litellm_model)
    if base:
        kwargs["api_base"] = base
    if key:
        kwargs["api_key"] = key
    if eff != litellm_model:
        kwargs["model"] = eff
    return kwargs


# ---------------------------------------------------------------------------
# litellm Router (production-recommended failover) — P1 upgrade
# ---------------------------------------------------------------------------
# The codebase historically called litellm.acompletion() with a manually-built
# per-fallback dict list. litellm's documented production pattern is a Router
# whose model_list declares each deployment with its OWN api_base/api_key (so
# endpoints/credentials can never leak across providers) and lets Router handle
# health-checked failover, cooldowns and retries. We build the model_list from
# the same get_live_fallbacks() chain so ordering/priorities are preserved.
_routers = {}
_router_lock = threading.Lock()


def _deployment_entry(model_id: str) -> dict:
    """litellm Router model_list entry — one deployment per provider with its own
    endpoint/key. Mirrors _fallback_entry() (no cross-provider base/key leak) but
    wrapped in litellm_params for Router consumption."""
    base, key, eff = _endpoint_for(model_id)
    params = {"model": eff}
    if base:
        params["api_base"] = base
    if key:
        params["api_key"] = key
    from runtime_v2.services.fallback_manager import _is_local_model

    if not _is_local_model(model_id):
        params["extra_body"] = {}
    return {"model_name": model_id, "litellm_params": params}


def build_router(primary_model: str, fallback_models: list) -> object:
    """Build (or reuse) a litellm.Router over primary + fallback deployments.
    Each entry carries its own endpoint/key so Router's failover never leaks a
    provider's credentials to another. Falls back to the raw acompletion path by
    returning None when litellm's Router API is unavailable."""
    global _routers
    model_list = [_deployment_entry(primary_model)]
    fallbacks_list = []
    for f in fallback_models or []:
        if f and f != primary_model:
            model_list.append(_deployment_entry(f))
            fallbacks_list.append(f)
    key = (primary_model, tuple(fallbacks_list))
    with _router_lock:
        if key in _routers:
            return _routers[key]
        try:
            router_fallbacks = (
                [{primary_model: fallbacks_list}] if fallbacks_list else None
            )
            _routers[key] = litellm.Router(
                model_list=model_list,
                fallbacks=router_fallbacks,
                routing_strategy="simple-shuffle",
                num_retries=0,
            )
            return _routers[key]
        except Exception as exc:
            log.warning(
                "litellm Router construction failed (%s) — using legacy acompletion",
                exc,
            )
            return None


def inject_system_prompt(messages: list, system: str) -> list:
    messages = list(messages)
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            messages[i] = {"role": "system", "content": system}
            return messages
    return [{"role": "system", "content": system}] + messages


async def complete_for_tool_decision(
    litellm_model: str, messages: list, fallbacks: list, agent_id: str = None
):
    # BUG FIX: "openai/..." is a shared prefix for local llama.cpp AND the OpenCode
    # Zen/Go cloud endpoints. Using startswith("openai/") misclassified the primary
    # cloud model (openai/deepseek-v4-flash) as LOCAL — sending llama.cpp-only
    # params (id_slot/n_predict/cache_prompt) and grammar response_format to the
    # OpenCode endpoint. Classify via _is_local_model() (matches the fallback split).
    from runtime_v2.services.fallback_manager import _is_local_model

    is_cloud = not _is_local_model(litellm_model)
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
        resp = await litellm.acompletion(**kwargs)
        try:
            from runtime_v2.services.usage_log import record_response

            record_response(resp, litellm_model, source="tool_decision")
        except Exception as usage_err:  # noqa: BLE001
            log.debug("usage log skipped: %s", usage_err)
        return resp

    async def _call_router(extra: dict, max_tokens: int):
        # P1: production-grade failover via litellm Router. Each deployment in the
        # model_list carries its own endpoint/key, so a primary outage genuinely
        # degrades to the next provider (the old flat-string fallback list sent
        # every provider to the primary's endpoint). Router returns None on
        # construction failure → we fall back to the legacy acompletion path.
        router = build_router(litellm_model, fallbacks)
        if router is not None:
            kwargs = {k: v for k, v in extra.items() if k != "messages"}
            kwargs["max_tokens"] = max_tokens
            kwargs["max_retries"] = 0
            kwargs["timeout"] = 300.0
            resp = await router.acompletion(
                model=litellm_model, messages=messages, **kwargs
            )
            try:
                from runtime_v2.services.usage_log import record_response

                record_response(resp, litellm_model, source="tool_decision")
            except Exception as usage_err:  # noqa: BLE001
                log.debug("usage log skipped: %s", usage_err)
            return resp
        return await _call(extra, max_tokens)

    is_strict_role = agent_id in ("coordinator", "planner", "tool-runner")
    local_temp = 0.0 if is_strict_role else 0.2
    cloud_temp = 0.0 if is_strict_role else 0.7

    extra = {
        "messages": messages,
        "temperature": cloud_temp if is_cloud else local_temp,
        "top_p": 0.95 if is_cloud else 0.9,
        "frequency_penalty": 0.1 if not is_cloud else 0.0,
        "presence_penalty": 0.1 if not is_cloud else 0.0,
    }
    if not is_cloud:
        extra["num_ctx"] = 16384
        extra["extra_body"] = {
            "max_tokens": local_max_tokens,
            "n_predict": local_max_tokens,
            "repeat_penalty": 1.15,
            "repeat_last_n": 256,
            "cache_prompt": False,
            "id_slot": -1,
            "frequency_penalty": 0.1,
            "presence_penalty": 0.1,
            # Disable the Qwen3/Qwen3.5/Qwen3.8 "thinking" block at the wire level.
            # /no_think in the prompt is a soft-switch these models drop; only
            # chat_template_kwargs.enable_thinking=false reliably yields prose in
            # content (verified live on the 3.8). Without it the model spends the
            # budget on an empty "Thinking Process:" scaffold inside
            # reasoning_content and returns content="".
            "chat_template_kwargs": {"enable_thinking": False},
        }
        # TASK 1 (opt-in grammar decode): constrain the local tool-decision path
        # to schema-valid JSON only. Cloud/DeepSeek never receives response_format.
        if _grammar_decode_enabled():
            extra["response_format"] = _grammar_response_format()
        return await _call(extra, local_max_tokens)
    extra["response_format"] = _cloud_response_format(litellm_model)
    try:
        # P1: cloud calls route through the Router (native failover across the
        # live fallback chain) when Router is available.
        return await _call_router(extra, cloud_max_tokens)
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
                litellm_model,
                affordable,
                clamped,
            )
            return await _call_router(extra, clamped)
        raise


async def stream_content(
    model: str, messages: list, agent_id: str
) -> AsyncGenerator[tuple[str, str], None]:
    litellm_model = get_litellm_model(agent_id, model)
    routing_mode = get_routing_mode()
    from runtime_v2.services.fallback_manager import get_live_fallbacks, _is_local_model

    raw_fallbacks = await get_live_fallbacks(mode=routing_mode)
    fallbacks = [f["model"] for f in raw_fallbacks if not _is_local_model(f["model"])][
        :4
    ]

    extra = {
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "repetition_penalty": 1.05,
        # "max_tokens": 32768,
        "num_ctx": 32768,
    }
    kwargs = build_kwargs(litellm_model, extra, fallbacks)
    kwargs["timeout"] = 900.0

    try:
        kwargs["max_retries"] = 0
        # P1: route through the litellm Router (native health-checked failover
        # across the live cloud chain) when available; fall back to the legacy
        # acompletion path otherwise.
        from runtime_v2.services.fallback_manager import _is_local_model as _isl

        is_cloud = not _isl(litellm_model)
        response = None
        if is_cloud:
            router = build_router(litellm_model, fallbacks)
            if router is not None:
                stream_kwargs = dict(extra)
                stream_kwargs["max_retries"] = 0
                stream_kwargs["timeout"] = 900.0
                response = await router.acompletion(
                    model=litellm_model, **stream_kwargs
                )
        if response is None:
            # Local streaming path: disable the Qwen3.x thinking block at the
            # wire level (chat_template_kwargs.enable_thinking=false — /no_think
            # alone is dropped by these models). Without it the stream yields
            # empty "Thinking Process:" scaffolds and no usable content.
            if not is_cloud and "extra_body" not in kwargs:
                kwargs["extra_body"] = {
                    **kwargs.get("extra_body", {}),
                    "chat_template_kwargs": {"enable_thinking": False},
                }
            response = await litellm.acompletion(**kwargs)
        last_usage = None
        async for chunk in response:
            # Capture usage BEFORE the empty-choices skip: OpenAI-compatible
            # streams emit the final usage on a standalone chunk with choices=[]
            # (dropping it would silently hide cost telemetry).
            if getattr(chunk, "usage", None):
                last_usage = chunk.usage
            if not chunk.choices:
                continue
            piece = chunk.choices[0].delta.content or ""
            if piece:
                yield piece, "content"
        try:
            from runtime_v2.services.usage_log import record_response

            if last_usage is not None:
                record_response(
                    {"usage": last_usage},
                    litellm_model,
                    source="stream_content",
                    agent_id=agent_id,
                )
        except Exception as usage_err:  # noqa: BLE001
            log.debug("usage log skipped: %s", usage_err)
    except Exception as exc:
        log.error("[%s] stream error: %s", agent_id, exc)
        yield str(exc), "error"


