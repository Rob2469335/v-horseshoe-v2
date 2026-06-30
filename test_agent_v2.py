import asyncio
from runtime_v2.api.agent_service_v2 import AgentServiceV2

async def main():
    service = AgentServiceV2()
    print("Testing pipeline delegation...")
    
    # We will trigger the reviewer stream to see if it yields agent_handoff on fail
    print("\n--- Testing Reviewer Handoff ---")
    messages = [{"role": "user", "content": "Please review this broken code and give VERDICT: FAIL"}]
    async for chunk in service.step_agent_stream("reviewer", "", history=messages):
        print(chunk)
        if chunk.get("type") == "agent_handoff":
            break

    print("\n--- Testing Tool Runner Delegation to Coder ---")
    messages = [{"role": "user", "content": "Tests failed, delegate back to coder to fix."}]
    async for chunk in service.step_agent_stream("tool-runner", "", history=messages):
        print(chunk)
        if chunk.get("type") == "agent_handoff" or chunk.get("type") == "final":
            break

if __name__ == "__main__":
    asyncio.run(main())
