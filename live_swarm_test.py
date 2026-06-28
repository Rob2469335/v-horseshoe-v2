import asyncio
import sys
import json
from runtime_v2.api.agent_service_v2 import AgentServiceV2

class MockOrchestrator:
    pass

async def live_test():
    svc = AgentServiceV2(orchestrator=MockOrchestrator())
    goal = "There is a logic bug in buggy_math.py. It sorts descending instead of ascending. Coordinate your agents to analyze it, fix the bug, test it, and review the final code."
    
    current_agent = "coordinator"
    prompt = goal
    history = []
    delegation_chain = ["coordinator"]
    
    print(f"\n--- GOAL: {goal} ---")
    
    for turn in range(10):
        print(f"\n[{current_agent.upper()} IS THINKING...]")
        
        next_agent = None
        task = ""
        content_accumulator = ""
        
        try:
            async for chunk in svc.step_agent_stream(current_agent, prompt, history=history, delegation_chain=delegation_chain):
                if chunk.get("type") == "content":
                    text = chunk.get("content", "")
                    content_accumulator += text
                    # Use safe encoding
                    sys.stdout.buffer.write(text.encode('utf-8', errors='replace'))
                    sys.stdout.flush()
                elif chunk.get("type") == "agent_handoff":
                    next_agent = chunk.get("to")
                    task = chunk.get("task", "")
                    sys.stdout.buffer.write(f"\n\n>>> DELEGATING TO {next_agent.upper()} >>>\n".encode('utf-8'))
                    sys.stdout.buffer.write(f"Task: {task}\n".encode('utf-8'))
                    sys.stdout.flush()
                elif chunk.get("type") == "error":
                    sys.stdout.buffer.write(f"\n[ERROR] {chunk.get('content')}\n".encode('utf-8'))
                    sys.stdout.flush()
        except Exception as e:
            sys.stdout.buffer.write(f"\n[EXCEPTION IN AGENT STREAM]: {e}\n".encode('utf-8', errors='replace'))
            sys.stdout.flush()
            break
            
        print("\n")
        
        if next_agent:
            current_agent = next_agent
            prompt = f"You have been delegated a task: {task}"
            delegation_chain.append(next_agent)
            history = []  # In a real run, history is managed differently, but fresh context is fine here
        else:
            print(f"[{current_agent.upper()} finished without delegating. End of chain.]")
            break

if __name__ == "__main__":
    asyncio.run(live_test())
