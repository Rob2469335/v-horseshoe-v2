"""OpenCode Go/Zen cloud-chain ordering + routing tests."""

import asyncio
import os
from unittest.mock import AsyncMock

import pytest

from runtime_v2.services import fallback_manager as fm


@pytest.fixture(autouse=True)
def reset_chain_cache():
    fm._cached_fallbacks = []
    fm._last_fetch_time = 0
    fm._cached_mode = None
    yield
    fm._cached_fallbacks = []
    fm._last_fetch_time = 0
    fm._cached_mode = None


def test_opencode_zen_free_lead_then_go_paid(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
    entries = fm._get_opencode_fallback()
    models = [e["model"] for e in entries]
    # Zen FREE deepseek-v4-flash first, then Go PAID deepseek-v4-flash.
    assert models == ["openai/zen/deepseek-v4-flash", "openai/deepseek-v4-flash"]


def test_opencode_only_emits_v4flash(monkeypatch):
    # Only v4-flash is allowed — no GLM/Kimi/Qwen/pro models.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
    entries = fm._get_opencode_fallback()
    for e in entries:
        assert "v4-flash" in e["model"]
        assert "glm" not in e["model"]
        assert "kimi" not in e["model"]
        assert "qwen" not in e["model"]
        assert "v4-pro" not in e["model"]


def test_opencode_fallback_noop_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert fm._get_opencode_fallback() == []


def test_zen_and_go_are_cloud_not_local():
    assert fm._is_local_model("openai/zen/deepseek-v4-flash") is False
    assert fm._is_local_model("openai/deepseek-v4-flash") is False


def test_build_kwargs_routes_zen_free_and_go_paid():
    from runtime_v2.services._llm_client import build_kwargs

    monkeypatch_env = {
        "OPENAI_API_BASE": "https://opencode.ai/zen/go/v1",
        "OPENAI_API_KEY": "sk-test",
    }
    old = {k: os.environ.get(k) for k in monkeypatch_env}
    os.environ.update(monkeypatch_env)
    try:
        zen = build_kwargs("openai/zen/deepseek-v4-flash", {"messages": []}, [])
        assert zen["api_base"] == "https://opencode.ai/zen/v1"
        assert zen["model"] == "openai/deepseek-v4-flash"
        assert zen["api_key"] == "sk-test"

        go = build_kwargs("openai/deepseek-v4-flash", {"messages": []}, [])
        assert go["api_base"] == "https://opencode.ai/zen/go/v1"
        assert go["model"] == "openai/deepseek-v4-flash"
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_chain_leads_with_free_flash_zen_go_paid(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")

    fm._fetch_openrouter_models = AsyncMock(return_value=[])
    fm._fetch_groq_models = AsyncMock(return_value=[])
    fm._fetch_nvidia_models = AsyncMock(
        return_value=[
            {
                "model": "nvidia_nim/deepseek-ai/deepseek-v4-flash",
                "context_length": 8192,
                "pricing": "API",
                "provider": "NVIDIA",
            },
        ]
    )
    fm._fetch_gemini_models = AsyncMock(return_value=[])
    fm._fetch_llama_models = AsyncMock(return_value=[])

    async def _run():
        await fm.refresh_fallbacks_if_needed()
        return [f["model"] for f in fm._cached_fallbacks]

    models = asyncio.run(_run())
    # The three v4-flash options lead INLINE: NVIDIA free -> Zen free -> Go paid.
    assert models[0] == "nvidia_nim/deepseek-ai/deepseek-v4-flash"
    assert models[1] == "openai/zen/deepseek-v4-flash"
    assert models[2] == "openai/deepseek-v4-flash"
    # DeepSeek direct (paid api.deepseek.com) is the LAST cloud entry.
    assert "deepseek/deepseek-v4-flash" in models
    assert models.index("deepseek/deepseek-v4-flash") > models.index(
        "openai/deepseek-v4-flash"
    )


def test_cache_is_keyed_by_routing_mode(monkeypatch):
    """A cache populated for one routing mode must never serve another mode.

    local_only produces a llama-only chain; auto/cloud_allowed produce the full
    cloud chain. Before the fix the cache was keyed only on a time TTL, so
    whichever mode refreshed first served the OTHER mode for the whole 30-min
    window (a `/local` session could be handed the cloud chain and vice-versa).
    """
    import time

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")

    # Simulate a fresh, already-populated local_only cache (llama-only chain).
    fm._cached_fallbacks = [{"model": "qwen3.5-4b"}]
    fm._last_fetch_time = time.time()
    fm._cached_mode = "local_only"

    # An `auto` refresh must NOT reuse that local_only cache — it must refetch
    # the full cloud chain. Prove it by the mocked fetchers being invoked.
    fetch_calls = {"auto": 0}

    async def _auto_fetch(*_args, **_kwargs):
        fetch_calls["auto"] += 1
        return [
            {
                "model": "nvidia_nim/deepseek-ai/deepseek-v4-flash",
                "context_length": 8192,
                "pricing": "API",
                "provider": "NVIDIA",
            }
        ]

    async def _empty_fetch(*_args, **_kwargs):
        return []

    monkeypatch.setattr(fm, "_fetch_nvidia_models", _auto_fetch)
    for name in (
        "_fetch_openrouter_models",
        "_fetch_groq_models",
        "_fetch_gemini_models",
        "_fetch_llama_models",
    ):
        monkeypatch.setattr(fm, name, _empty_fetch)

    async def _run():
        await fm.refresh_fallbacks_if_needed(mode="auto")
        return list(fm._cached_fallbacks)

    models = asyncio.run(_run())
    assert fetch_calls["auto"] == 1, (
        "auto mode must refetch, not reuse the local_only cache"
    )
    assert fm._cached_mode == "auto"
    assert any("nvidia" in m["model"] for m in models), (
        "cache must now hold the cloud chain, not llama-only"
    )

    # A second `auto` refresh within TTL with the SAME mode reuses the cache.
    async def _run2():
        await fm.refresh_fallbacks_if_needed(mode="auto")

    asyncio.run(_run2())
    assert fetch_calls["auto"] == 1, "same-mode refresh within TTL must reuse the cache"


def test_fallbacks_scoped_to_own_endpoint_no_cross_provider_leak():
    """Every fallback must be scoped to its own endpoint/key. A flat-string
    fallback list made litellm reuse the PRIMARY's api_base/api_key on every
    provider (NVIDIA/Groq/Gemini hit the OpenCode URL and received the OpenCode
    key). Native providers must carry NO explicit api_base/api_key so litellm
    uses its own provider config; OpenCode entries carry their own endpoint."""
    from runtime_v2.services._llm_client import build_kwargs

    monkeypatch_env = {
        "OPENAI_API_BASE": "https://opencode.ai/zen/go/v1",
        "OPENAI_API_KEY": "sk-opencode",
    }
    old = {k: os.environ.get(k) for k in monkeypatch_env}
    os.environ.update(monkeypatch_env)
    try:
        kwargs = build_kwargs(
            "openai/deepseek-v4-flash",
            {"messages": []},
            [
                "nvidia_nim/deepseek-ai/deepseek-v4-flash",
                "groq/meta-llama/llama-prompt-guard-2-86m",
                "openai/zen/deepseek-v4-flash",
            ],
        )
        fbs = kwargs["fallbacks"]
        # Primary keeps its own Go base + key.
        assert kwargs["api_base"] == "https://opencode.ai/zen/go/v1"
        assert kwargs["api_key"] == "sk-opencode"
        # Native NVIDIA/Groq fallbacks: NO api_base, NO api_key (no leak).
        nvidia, groq, zen = fbs
        assert nvidia["model"] == "nvidia_nim/deepseek-ai/deepseek-v4-flash"
        assert "api_base" not in nvidia and "api_key" not in nvidia
        assert groq["model"] == "groq/meta-llama/llama-prompt-guard-2-86m"
        assert "api_base" not in groq and "api_key" not in groq
        # OpenCode Zen fallback: carries its OWN base + key.
        assert zen["model"] == "openai/deepseek-v4-flash"
        assert zen["api_base"] == "https://opencode.ai/zen/v1"
        assert zen["api_key"] == "sk-opencode"
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
