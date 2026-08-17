"""Tests for orchestrator bug fixes:
1. Duplicate slash-command loop detection
2. Cloud provider routing (openrouter/free should NOT go to Ollama)
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from swarm_os.core.orchestrator import Orchestrator


@pytest.fixture
def orch():
    """Build an Orchestrator with mocked-out dependencies."""
    with (
        patch("swarm_os.core.orchestrator.LlamaClient") as MockLlama,
        patch("swarm_os.core.orchestrator.MemoryBridge") as MockBridge,
        patch("swarm_os.core.orchestrator.mcp_registry") as MockMCP,
        patch("swarm_os.core.orchestrator.EventStore"),
        patch("swarm_os.core.orchestrator.TraceCollector"),
    ):
        MockBridge.return_value.get_memory_context = AsyncMock(return_value="")
        MockMCP.get_tools_schema.return_value = []
        o = Orchestrator()
        o.llm = MockLlama.return_value
        o.ollama = o.llm
        return o


# ─────────────────────────────────────────────────────────
# BUG #1: Duplicate slash-command loop detection
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# BUG #3: Successful generations never feed the bandit (router.record_success
# was never called — only record_failure — so total_requests climbed while
# successes stayed 0, and a model that failed once could never recover its
# standing: strategy.py scores success_rate = successes / total_requests)
# ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_records_success_and_recovers_model_standing(orch):
    """A successful generation must advance the router's successes counter so
    a model that previously failed can recover its standing (real effect:
    success_rate moves off 0.0, not just 'record_success was called')."""

    orch.llm.generate = AsyncMock(return_value="Plain text success response")
    orch.critic.evaluate_step = MagicMock(
        return_value=MagicMock(accepted=True, reason="ok")
    )

    # Simulate a prior failure on the model: failures=1, total=1, success_rate=0.0
    orch.router.record_failure(model="qwen3.5-4b", cooldown_seconds=0.0)
    before = orch.router.get_state("qwen3.5-4b")
    assert before.successes == 0
    assert before.total_requests == 1

    result, model = await orch.generate(model="qwen3.5-4b", prompt="hello")

    assert result == "Plain text success response"
    after = orch.router.get_state("qwen3.5-4b")
    # The fix: this success actually advanced the bandit state
    assert after.successes == 1, (
        f"record_success did not advance successes: {after.successes}"
    )
    assert after.total_requests == 2
    success_rate = after.successes / after.total_requests
    assert success_rate == 0.5, f"success_rate did not recover from 0.0: {success_rate}"
    # Cooldown from the prior failure must have been cleared by the success
    import time as _time

    assert after.cooldown_until <= _time.time()


@pytest.mark.asyncio
async def test_slash_command_not_repeated(orch):
    """If the model emits the same slash command JSON every turn,
    the orchestrator must break out after the first handled call,
    NOT loop 5 times."""

    slash_json = '{"command":"/goal fix the routes","confidence":0.8}'

    # Mock: llm.generate returns the same slash-command JSON every time
    orch.llm.generate = AsyncMock(return_value=slash_json)

    # Mock critic to accept
    orch.critic.evaluate_step = MagicMock(
        return_value=MagicMock(accepted=True, reason="ok")
    )

    result, model = await orch.generate(
        model="qwen3.5-4b",
        prompt="fix the routes",
    )

    # The model was called at most ONCE (slash command breaks immediately after handled)
    assert orch.llm.generate.call_count == 1, (
        f"Expected 1 call (break after slash command handled), got {orch.llm.generate.call_count}"
    )
    assert result == slash_json


@pytest.mark.asyncio
async def test_duplicate_tool_call_breaks_loop(orch):
    """If the model re-emits the exact same NON-command tool call after it
    was already handled, the orchestrator detects the duplicate and breaks."""

    tool_xml = '<tool_call name="search">{"query":"hello"}</tool_call>'

    orch.llm.generate = AsyncMock(return_value=tool_xml)
    orch.critic.evaluate_step = MagicMock(
        return_value=MagicMock(accepted=True, reason="ok")
    )
    orch.mcp.call = AsyncMock(return_value={"ok": True, "data": "result"})

    result, model = await orch.generate(
        model="qwen3.5-4b",
        prompt="search for hello",
    )

    # First call: tool executed. Second call: duplicate detected → break.
    assert orch.llm.generate.call_count == 2, (
        f"Expected 2 calls (first executes, second detects dup), got {orch.llm.generate.call_count}"
    )


# ─────────────────────────────────────────────────────────
# BUG #2: Provider detection / routing
# ─────────────────────────────────────────────────────────


def test_detect_provider_openrouter_free(orch):
    assert orch._detect_provider("openrouter/free") in ("openrouter", "llama")
    # If OPENROUTER_API_KEY is set, it should be openrouter


def test_detect_provider_nvidia(orch):
    assert orch._detect_provider("nvidia/llama-3.1-nemotron-nano-8b-v1") in (
        "nvidia",
        "llama",
    )


def test_detect_provider_local_model(orch):
    assert orch._detect_provider("qwen3.5-4b") == "llama"


def test_detect_provider_meta_model(orch):
    assert orch._detect_provider("meta/llama-3.3-70b-instruct") == "openrouter"


def test_detect_provider_deepseek(orch):
    assert orch._detect_provider("deepseek/deepseek-v4-flash") in (
        "openrouter",
        "llama",
    )


@pytest.mark.asyncio
async def test_openrouter_free_not_sent_to_llm(orch, monkeypatch):
    """openrouter/free must NOT be sent to LlamaClient.generate."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-key-12345")

    # _cloud_generate should be called instead of llm.generate
    orch._cloud_generate = AsyncMock(return_value="Cloud response OK")
    orch.llm.generate = AsyncMock(
        side_effect=AssertionError("LLM should not be called for openrouter/free")
    )

    result, model = await orch.generate(
        model="openrouter/free",
        prompt="hello",
    )

    assert result == "Cloud response OK"
    orch._cloud_generate.assert_called_once()
    orch.llm.generate.assert_not_called()


@pytest.mark.asyncio
async def test_generate_fallback_when_no_api_key(orch, monkeypatch):
    """If no OPENROUTER_API_KEY is set, openrouter/free should fall back to a local model."""

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    orch.llm.generate = AsyncMock(return_value="Local fallback response")

    result, model = await orch.generate(
        model="openrouter/free",
        prompt="hello",
    )

    assert result == "Local fallback response"
    orch.llm.generate.assert_called_once()
    # Model should have been changed to local fallback
    call_args = orch.llm.generate.call_args
    call_model = call_args.kwargs.get("model") or (
        call_args.args[0] if call_args.args else ""
    )
    assert call_model != "openrouter/free", f"Expected local fallback, got {call_model}"
