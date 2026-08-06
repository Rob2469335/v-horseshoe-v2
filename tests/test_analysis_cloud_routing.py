"""Tests for routing analysis agents to a cloud model (DeepSeek V4 flash via
the funded OpenCode Go account)."""
import os
from unittest.mock import patch

from runtime_v2.services._llm_client import get_litellm_model


def _patch_env(**overrides):
    return patch.dict(os.environ, overrides, clear=False)


def test_analysis_agent_routes_to_cloud_when_key_present():
    with _patch_env(OPENAI_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="auto", SWARM_ROUTING_MODE="auto"):
        for agent in ("code_analyzer", "researcher", "reviewer"):
            assert get_litellm_model(agent, "qwen3.5-4b") == "openai/deepseek-v4-flash"


def test_edit_agents_route_to_cloud_when_key_present():
    # coder/debugger need strong instruction-following for the read->edit->
    # verify protocol; the local 4B reproduced the /upgrade dead-loop instead.
    with _patch_env(OPENAI_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="auto", SWARM_ROUTING_MODE="auto"):
        for agent in ("coder", "debugger"):
            assert get_litellm_model(agent, "qwen3.5-4b") == "openai/deepseek-v4-flash"


def test_executor_routes_to_cloud_when_key_present():
    # executor now orchestrates compound goals (chaining researcher->coder->
    # tool-runner); the local 4B cannot follow a multi-agent chain reliably.
    with _patch_env(OPENAI_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="auto", SWARM_ROUTING_MODE="auto"):
        assert get_litellm_model("executor", "qwen3.5-4b") == "openai/deepseek-v4-flash"


def test_analysis_agent_stays_local_without_cloud_key():
    with _patch_env(OPENAI_API_KEY="", SWARM_ANALYSIS_CLOUD="auto", SWARM_ROUTING_MODE="auto"):
        for agent in ("code_analyzer", "researcher", "reviewer", "coder", "debugger"):
            assert get_litellm_model(agent, "qwen3.5-4b") == "openai/qwen3.5-4b"


def test_analysis_agent_stays_local_in_local_only_mode():
    with _patch_env(OPENAI_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="auto", SWARM_ROUTING_MODE="local_only"):
        for agent in ("code_analyzer", "researcher", "reviewer", "coder", "debugger"):
            assert get_litellm_model(agent, "qwen3.5-4b") == "openai/qwen3.5-4b"


def test_analysis_cloud_can_be_explicitly_disabled():
    with _patch_env(OPENAI_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="off", SWARM_ROUTING_MODE="auto"):
        for agent in ("code_analyzer", "coder"):
            assert get_litellm_model(agent, "qwen3.5-4b") == "openai/qwen3.5-4b"


def test_non_analysis_agent_stays_local_even_with_cloud_key():
    with _patch_env(OPENAI_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="auto", SWARM_ROUTING_MODE="auto"):
        for agent in ("coordinator", "planner", "tool-runner", "tool-maker"):
            assert get_litellm_model(agent, "qwen3.5-4b") == "openai/qwen3.5-4b"


def test_cloud_model_env_override():
    with _patch_env(OPENAI_API_KEY="sk-test", ANALYSIS_CLOUD_MODEL="openrouter/deepseek/deepseek-r1:free", SWARM_ROUTING_MODE="auto"):
        assert get_litellm_model("researcher", "qwen3.5-4b") == "openrouter/deepseek/deepseek-r1:free"


def test_force_local_skips_analysis_cloud():
    # Billing-402 degrade: force_local=True must bypass the analysis-cloud hop and
    # return the local llama.cpp model even when a cloud key is present.
    from runtime_v2.services._llm_client import get_litellm_model
    with _patch_env(OPENAI_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="auto", SWARM_ROUTING_MODE="auto"):
        assert get_litellm_model("code_analyzer", "qwen3.5-4b", force_local=True) == "openai/qwen3.5-4b"


def test_opencode_go_flash_classified_as_cloud_not_local():
    # Regression: `openai/deepseek-v4-flash` (the primary analysis-cloud model)
    # used to be classified as LOCAL by startswith("openai/"), sending llama.cpp
    # grammar/params to the OpenCode endpoint. Must be cloud.
    from runtime_v2.services.fallback_manager import _is_local_model
    from runtime_v2.services._llm_client import complete_for_tool_decision
    import inspect
    assert _is_local_model("openai/deepseek-v4-flash") is False
    src = inspect.getsource(complete_for_tool_decision)
    assert "_is_local_model(litellm_model)" in src
