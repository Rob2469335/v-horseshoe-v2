#!/usr/bin/env python3
"""Test script to verify the fixed agent system."""
import asyncio
import logging
from runtime_v2.api.agent_service_v2 import AgentServiceV2

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

async def test_agents():
    """Test all 8 agents."""
    service = AgentServiceV2()
    
    agents = service.list_agents()
    log.info(f"Registered agents: {[a['id'] for a in agents]}")
    
    assert len(agents) == 8, f"Expected 8 agents, got {len(agents)}"
    
    agent_ids = [a['id'] for a in agents]
    expected = ["coordinator", "planner", "researcher", "executor", "coder", "tool-runner", "reviewer", "debugger"]
    for agent_id in expected:
        assert agent_id in agent_ids, f"Missing agent: {agent_id}"
    
    log.info("✓ All 8 agents registered correctly")
    
    # Test stream with a simple prompt
    log.info("Testing coordinator agent...")
    chunks = []
    async for chunk in service.step_agent_stream("coordinator", "Hello, what's your name?"):
        chunks.append(chunk)
        if chunk.get("type") == "error":
            log.error(f"Error: {chunk.get('content')}")
        elif chunk.get("type") == "model_selected":
            log.info(f"Model: {chunk.get('model')}")
        elif chunk.get("content"):
            log.info(f"Output: {chunk.get('content')[:100]}")
    
    log.info(f"✓ Coordinator completed with {len(chunks)} events")
    
    return True

if __name__ == "__main__":
    try:
        result = asyncio.run(test_agents())
        print("\n✅ All tests passed!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
