import asyncio
from runtime_v2.api.agent_service_v2 import AgentServiceV2
from runtime_v2.services.model_registry import _AGENT_MODELS
import runtime_v2.services.stream_runner  # Trigger the monkey patches

async def main():
    service = AgentServiceV2()
    print("Testing all 7 agents in the registry...")
    
    agents = list(_AGENT_MODELS.keys())
    
    for agent in agents:
        print(f"\n--- Testing Agent: {agent} ({_AGENT_MODELS[agent][0]}) ---")
        try:
            messages = [{"role": "user", "content": "Reply with exactly one word: OK"}]
            success = False
            async for chunk in service.step_agent_stream(agent, "", history=messages):
                if chunk.get("type") in ["content", "tool_call", "agent_handoff", "final"]:
                    print(f"[{agent}] Responded with type: {chunk.get('type')}")
                    if chunk.get("type") == "content":
                        print(f"[{agent}] Content: {chunk.get('content')}")
                    success = True
                    break
                elif chunk.get("type") == "error":
                    print(f"[{agent}] ERROR: {chunk.get('content')}")
                    break
            if not success:
                print(f"[{agent}] No valid response received.")
        except Exception as e:
            print(f"[{agent}] Exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
