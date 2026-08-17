import os
import pytest


@pytest.mark.asyncio
async def test_semantic_cache_smoke():
    # 1. Enable SWARM_SEMANTIC_CACHE in the environment
    os.environ["SWARM_SEMANTIC_CACHE"] = "1"

    # 2. Reset the cache state
    from runtime_v2.services import _semantic_decision_cache

    _semantic_decision_cache._decision_cache.clear()

    import uuid

    uid1 = uuid.uuid4().hex
    uid2 = uuid.uuid4().hex
    uid3 = uuid.uuid4().hex
    agent_id = f"test_agent_{uid1[:8]}"
    messages = [
        {
            "role": "user",
            "content": f"Random prompt to avoid semantic collision: {uid1} {uid2} {uid3}",
        }
    ]
    mock_decision = {"action": "final", "response": "Paris"}

    # 3. Assert initial cache miss
    cached = await _semantic_decision_cache.get_semantic_cached_decision(
        messages, agent_id
    )
    assert cached is None, "Expected cache miss on first run"

    # 4. Cache the decision (simulating LLM fallback behavior)
    # The message must be >10 chars.
    await _semantic_decision_cache.cache_tool_decision(
        messages, agent_id, mock_decision
    )

    # 5. Assert cache hit
    cached = await _semantic_decision_cache.get_semantic_cached_decision(
        messages, agent_id
    )
    assert cached is not None, "Expected cache hit on second run"
    assert cached["response"] == "Paris", "Cache hit should return the stored decision"

    # 6. Check that a different agent/message misses
    messages2 = [
        {
            "role": "user",
            "content": "Please rewrite this python script to use asyncio and async/await syntax! It currently uses threading.",
        }
    ]
    cached2 = await _semantic_decision_cache.get_semantic_cached_decision(
        messages2, agent_id
    )
    assert cached2 is None, "Expected cache miss for different prompt"

    # 7. Check sanitization
    messages_secret = [{"role": "user", "content": "My secret is Bearer xyz123"}]
    decision_secret = {"action": "final", "response": "I saved the secret."}
    await _semantic_decision_cache.cache_tool_decision(
        messages_secret, "test_agent", decision_secret
    )
    cached_secret = await _semantic_decision_cache.get_semantic_cached_decision(
        messages_secret, "test_agent"
    )
    assert cached_secret is None, "Expected cache miss due to sanitization bypass"

    # Cleanup
    os.environ.pop("SWARM_SEMANTIC_CACHE", None)
