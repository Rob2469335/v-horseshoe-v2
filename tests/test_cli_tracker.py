"""CLI token-tracker provider classification + routing default tests."""

import os
from unittest.mock import patch

from organism_console.token_tracker import _classify_provider


def _cls(model):
    return _classify_provider(model)


def test_local_qwen_is_local_not_paid():
    # Regression: `openai/qwen3.5-4b` (and bare `qwen3.5-4b`) used to snap into
    # the `openrouter_paid` bucket because any `/` model without `:free` matched.
    assert _cls("openai/qwen3.5-4b") == "ollama_local"
    assert _cls("qwen3.5-4b") == "ollama_local"


def test_deepseek_direct_has_own_bucket():
    assert _cls("deepseek/deepseek-v4-flash") == "deepseek"


def test_openrouter_deepseek_is_openrouter_paid():
    assert _cls("openrouter/deepseek/deepseek-v4-flash") == "openrouter_paid"


def test_ling_free_vs_paid():
    assert _cls("openrouter/inclusionai/ling-3.0-flash:free") == "openrouter_free"
    assert _cls("openrouter/inclusionai/ling-2.6-flash") == "openrouter_paid"


def test_nvidia_nim_is_nvidia():
    assert _cls("nvidia_nim/deepseek-ai/deepseek-v4-flash") == "nvidia"


def test_openai_deepseek_is_paid():
    assert _cls("openai/deepseek-chat") == "openai_paid"


def test_opencode_zen_go_are_paid_cloud():
    assert _cls("openai/zen/deepseek-v4-flash") == "openai_paid"
    assert _cls("openai/deepseek-v4-flash") == "openai_paid"


def test_default_routing_mode_is_auto():
    # Cloud fan-out (DeepSeek first, ultra-cheap Ling after) is the default so it
    # is actually used; /local still forces local_only.
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SWARM_ROUTING_MODE", None)
        from runtime_v2.services._llm_client import get_routing_mode
        assert get_routing_mode() == "auto"
