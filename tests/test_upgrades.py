from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from swarm_os.services.orchestrator import Orchestrator
from swarm_os.services.control_plane.strategy_registry import strategy_registry
from swarm_os.services.control_plane.strategy import BanditStrategy
from swarm_os.services.control_plane.models import ModelProfile
from qdrant_client import models as qdrant_models


@pytest.mark.asyncio
async def test_bandit_strategy_registration():
    assert strategy_registry.has("bandit")
    strategy = strategy_registry.get("bandit")
    assert isinstance(strategy, BanditStrategy)

@pytest.mark.asyncio
async def test_bandit_strategy_selection():
    # Setup a Router with two model profiles
    from swarm_os.services.control_plane.router import Router
    router = Router(
        profiles=[
            ModelProfile(name="model-a", role="fast", max_tokens=4000),
            ModelProfile(name="model-b", role="fast", max_tokens=4000),
        ],
        default_role="fast"
    )
    
    # Mock model-a as highly successful, model-b as having failures
    state_a = router.get_state("model-a")
    state_a.successes = 10
    state_a.total_requests = 10
    state_a.total_latency_ms = 1000.0 # 100ms avg
    
    state_b = router.get_state("model-b")
    state_b.failures = 5
    state_b.total_requests = 5
    
    # Epsilon = 0 (Pure exploitation)
    strategy = BanditStrategy(epsilon=0.0)
    decision = strategy.select_model(
        router=router,
        candidates=["model-a", "model-b"],
        role="fast"
    )
    assert decision.model == "model-a"
    assert decision.reason == "bandit_exploitation"
    
    # Epsilon = 1 (Pure exploration - should select randomly)
    strategy_explore = BanditStrategy(epsilon=1.0)
    decisions = [
        strategy_explore.select_model(router=router, candidates=["model-a", "model-b"], role="fast").model
        for _ in range(50)
    ]
    assert "model-a" in decisions
    assert "model-b" in decisions

@pytest.mark.asyncio
async def test_token_budget_exceeded():
    orchestrator = Orchestrator()
    orchestrator.token_manager._total_used = 1000
    orchestrator.token_manager._budget = 500
    
    with pytest.raises(ValueError, match="Token budget exceeded"):
        await orchestrator.generate(model="qwen3.5-4b", prompt="Hello")
        
    # Stream generate should yield an error chunk
    chunks = []
    async for chunk, _, _ in orchestrator.stream_generate(model="qwen3.5-4b", prompt="Hello"):
        chunks.append(chunk)
    full_output = "".join(chunks)
    assert "Token budget exceeded" in full_output

@pytest.mark.asyncio
async def test_dynamic_tool_schema_injection():
    orchestrator = Orchestrator()
    orchestrator._get_memory_context = AsyncMock(return_value="")
    orchestrator.llm.generate = AsyncMock(return_value="Final Answer")
    
    messages = [{"role": "user", "content": "Query"}]
    await orchestrator.generate(model="qwen3.5-4b", messages=messages)
    
    # Check that schemas were injected into the message content
    call_args = orchestrator.llm.generate.call_args.kwargs
    injected_content = call_args["messages"][0]["content"]
    assert "Available MCP Tools" in injected_content
    assert "filesystem" in injected_content
    assert "playwright" in injected_content

@pytest.mark.asyncio
async def test_critic_reflection_loop(tmp_path):
    orchestrator = Orchestrator()
    # BUG FIX: orchestrator.mcp is the module-level shared MCPRegistry singleton
    # (orchestrator.py:71 `self.mcp = mcp_registry`). Mutating its root here used
    # to leak into every later test in the process (a filesystem read of
    # 'swarm_os/foo.py' failed with tmp_path as root). Restore the real root after.
    _orig_root = orchestrator.mcp.root
    orchestrator.mcp.root = tmp_path
    try:
        orchestrator._get_memory_context = AsyncMock(return_value="")
    
        # Turn 1: Return invalid tool call that will fail (re-write to a folder that does not exist or empty list)
        # Turn 2: Final response
        tool_call_invalid = '<tool_call name="filesystem">{"operation": "read", "path": "does_not_exist.txt"}</tool_call>'
        final_response = "I see the file does not exist, so I am reporting back."
    
        orchestrator.llm.generate = AsyncMock(side_effect=[
            tool_call_invalid,
            final_response,
            AssertionError("generate() called a 3rd time -- loop did not break on plain-text response"),
        ])
    
        messages = [{"role": "user", "content": "Read non_existent file"}]
        result, _ = await orchestrator.generate(model="qwen3.5-4b", messages=messages)
    
        assert result == final_response
        # Assert that a critic corrective prompt was added to the history in the second call
        assert orchestrator.llm.generate.call_count == 2
        second_call_messages = orchestrator.llm.generate.call_args_list[1][1]["messages"]
        critic_feedbacks = [m for m in second_call_messages if "Critic Feedback" in m.get("content", "")]
        assert critic_feedbacks
        assert "Tool error" in critic_feedbacks[0]["content"]
    finally:
        orchestrator.mcp.root = _orig_root



@pytest.mark.asyncio
async def test_memory_context_keyword_boosting():
    orchestrator = Orchestrator()
    # Mock MemoryBridge to use in-memory vector store hits
    orchestrator.bridge._embed = AsyncMock(return_value=[0.1] * 768)
    
    mock_hits = [
        {
            "score": 0.5,
            "payload": {
                "summary": "Ran upwork crawler task",
                "models": ["qwen3.5-4b"],
                "dominant_outcome": "success"
            }
        },
        {
            "score": 0.6,
            "payload": {
                "summary": "File editing failure",
                "models": ["qwen3.5-4b"],
                "dominant_outcome": "failure"
            }
        }
    ]
    orchestrator.bridge.vs.search = AsyncMock(return_value=mock_hits)
    
    # Query contains "upwork" -> should boost the upwork hit score
    context = await orchestrator._get_memory_context("upwork task runs")
    
    # Without boost, "File editing failure" has score 0.6 (higher than 0.5).
    # With boost, "Ran upwork crawler task" gets +0.05 (matching 'upwork') +0.05 (matching 'task') -> score 0.6.
    # Outcome 'success' does not match query.
    # Therefore, "Ran upwork crawler task" becomes highly relevant and is listed first.
    assert "Ran upwork crawler task" in context

@pytest.mark.asyncio
async def test_memory_consolidation():
    # Setup MemoryBridge with in-memory VectorStore and Mock QdrantClient
    from swarm_os.memory.memory_bridge import MemoryBridge
    from swarm_os.services.vector_store import VectorStore
    
    vs = VectorStore(collection_name="test_consolidation", use_memory=True)
    await vs._wait_init()
    bridge = MemoryBridge(event_log_path="test_events.jsonl", vector_store=vs)
    bridge._embed = AsyncMock(return_value=[0.1] * 768)
    
    # Insert multiple individual memory runs
    vec = [0.1] * 768
    await vs.client.upsert(
        collection_name="test_consolidation",
        points=[
            qdrant_models.PointStruct(id=1, vector=vec, payload={"summary": "task a", "dominant_outcome": "success", "event_count": 5}),
            qdrant_models.PointStruct(id=2, vector=vec, payload={"summary": "task b", "dominant_outcome": "success", "event_count": 4}),
            qdrant_models.PointStruct(id=3, vector=vec, payload={"summary": "task c", "dominant_outcome": "failure", "event_count": 2}),
        ]
    )

    
    # Mock Ollama summarization endpoint
    bridge.http.post = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={"choices": [{"message": {"content": "Unified task runs successfully completed."}}]})
    bridge.http.post.return_value = mock_resp
    
    # Run consolidation
    consolidated = await bridge.consolidate_memories()
    assert consolidated is True
    
    # Check that individual success runs (task a, task b) were deleted and replaced by a consolidated summary
    info, _ = await vs.client.scroll(collection_name="test_consolidation")
    assert len(info) == 2 # 1 failure run (not consolidated because count < 2) + 1 new consolidated run
    
    # Verify consolidated payload contains combined fields
    consolidated_run = [r for r in info if r.payload.get("consolidated")][0]
    assert consolidated_run.payload["summary"] == "Unified task runs successfully completed."
    assert consolidated_run.payload["event_count"] == 9


@pytest.mark.asyncio
async def test_close_cancels_pending_bg_tasks(tmp_path):
    """MemoryBridge.close() must cancel pending _spawn() graph tasks.

    The fire-and-forget graph writes are tracked in `_bg_tasks` so they aren't
    GC'd mid-flight. Before the fix, close() tore down the httpx/embedding
    clients without cancelling those tasks — a pending graph write could run
    against closed clients, and Python warned 'Task was destroyed but it is
    pending' on shutdown. close() now cancels + awaits the pending tasks first."""
    from swarm_os.memory.memory_bridge import MemoryBridge

    bridge = MemoryBridge(
        event_log_path=tmp_path / "events.jsonl",
        vector_store=MagicMock(),
        embedding_svc=MagicMock(),
    )
    bridge.event_repo.save_state = MagicMock()
    bridge.http = AsyncMock()
    bridge.http.aclose = AsyncMock()
    bridge.emb = AsyncMock()
    bridge.emb.aclose = AsyncMock()

    started = asyncio.Event()

    async def never_ends():
        started.set()
        await asyncio.Event().wait()

    task = bridge._spawn(never_ends())
    assert task is not None
    await started.wait()

    await bridge.close()
    assert task.cancelled(), "close() must cancel pending _bg_tasks"
