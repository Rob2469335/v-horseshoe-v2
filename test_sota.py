import asyncio
import os
import json
from runtime_v2.api.agent_service_v2 import AgentServiceV2
from runtime_v2.services.memory_core import get_relevant_memories, _moe_route_shards

async def test_memory_routing():
    print("--- TESTING MoE MEMORY ROUTING ---")
    query = "How do I fix a bug in the code?"
    shards = _moe_route_shards(query)
    print(f"Query: '{query}' -> Shards Routed: {shards}")
    
    query2 = "What are my preferences?"
    shards2 = _moe_route_shards(query2)
    print(f"Query: '{query2}' -> Shards Routed: {shards2}")

async def test_circuit_breaker():
    print("\n--- TESTING CIRCUIT BREAKER ---")
    # We will instantiate AgentServiceV2 and mock run_tool
    service = AgentServiceV2(None)
    
    # We will pass a prompt to a simple agent
    prompt = "Use sandbox_repl to evaluate '1/0'. Since this will raise ZeroDivisionError, do it 3 times to see if the circuit breaker catches it."
    
    print("Initiating agent stream...")
    try:
        async for chunk in service.step_agent_stream("tool-runner", prompt):
            if chunk.get("type") == "tool_result":
                print(f"[TOOL] {chunk.get('tool')}: ok={chunk.get('result', {}).get('ok', False)}")
            elif chunk.get("type") == "error":
                print(f"\n[CIRCUIT BREAKER ACTIVATED] {chunk.get('content')}")
            elif chunk.get("type") == "agent_handoff":
                print(f"[HANDOFF] -> {chunk.get('to')}: {chunk.get('task')}")
            else:
                pass
    except Exception as e:
        print(f"Stream finished or error: {e}")

if __name__ == "__main__":
    asyncio.run(test_memory_routing())
    asyncio.run(test_circuit_breaker())
