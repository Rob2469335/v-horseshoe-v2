"""Tests for routing analysis agents to a cloud model (DeepSeek V4 flash)."""
import os
from unittest.mock import patch

from runtime_v2.services._llm_client import get_litellm_model


def _patch_env(**overrides):
    return patch.dict(os.environ, overrides, clear=False)


def test_analysis_agent_routes_to_cloud_when_key_present():
    with _patch_env(OPENROUTER_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="auto"):
        for agent in ("code_analyzer", "researcher", "reviewer"):
            assert get_litellm_model(agent, "qwen3.5-9b") == "openrouter/deepseek/deepseek-chat"


def test_analysis_agent_stays_local_without_cloud_key():
    with _patch_env(OPENROUTER_API_KEY="", SWARM_ANALYSIS_CLOUD="auto"):
        for agent in ("code_analyzer", "researcher", "reviewer"):
            assert get_litellm_model(agent, "qwen3.5-9b") == "openai/qwen3.5-9b"


def test_analysis_agent_stays_local_in_local_only_mode():
    with _patch_env(OPENROUTER_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="auto", SWARM_ROUTING_MODE="local_only"):
        for agent in ("code_analyzer", "researcher", "reviewer"):
            assert get_litellm_model(agent, "qwen3.5-9b") == "openai/qwen3.5-9b"


def test_analysis_cloud_can_be_explicitly_disabled():
    with _patch_env(OPENROUTER_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="off"):
        assert get_litellm_model("code_analyzer", "qwen3.5-9b") == "openai/qwen3.5-9b"


def test_non_analysis_agent_stays_local_even_with_cloud_key():
    with _patch_env(OPENROUTER_API_KEY="sk-test", SWARM_ANALYSIS_CLOUD="auto"):
        for agent in ("coordinator", "coder", "debugger", "executor"):
            assert get_litellm_model(agent, "qwen3.5-9b") == "openai/qwen3.5-9b"


def test_cloud_model_env_override():
    with _patch_env(OPENROUTER_API_KEY="sk-test", ANALYSIS_CLOUD_MODEL="openrouter/deepseek/deepseek-r1:free"):
        assert get_litellm_model("researcher", "qwen3.5-9b") == "openrouter/deepseek/deepseek-r1:free"
