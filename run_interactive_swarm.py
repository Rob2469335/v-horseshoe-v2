import asyncio
import sys
import logging
from runtime_v2.api.agent_service_v2 import AgentServiceV2

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("InteractiveSwarm")

class MockOrchestrator:
    pass

async def main():
    print("=== SWARM OS v3 : HITL GATEWAY ===")
    print("Initializing interactive agent session...\n")
    svc = AgentServiceV2(orchestrator=MockOrchestrator())
    
    current_agent = "planner"
    prompt = "I need to configure a new database. Use the ask_user tool to ask me for the database URL and credentials."
    
    history = []
    delegation_chain = [current_agent]
    
    print(f"[{current_agent.upper()}] Initial Task: {prompt}\n")
    
    while True:
        next_agent = None
        task_str = ""
        asked_user = False
        question = ""
        
        try:
            async for chunk in svc.step_agent_stream(current_agent, prompt, history=history, delegation_chain=delegation_chain):
                if chunk.get("type") == "agent_handoff":
                    next_agent = chunk.get("to")
                    task_str = chunk.get("task", "")
                    print(f"\n[{current_agent}] -> DELEGATING to [{next_agent}]: {task_str}\n")
                elif chunk.get("type") == "final":
                    print(f"\n[{current_agent}] -> FINAL RESULT: {chunk.get('content')}\n")
                elif chunk.get("type") == "tool_call":
                    print(f"[{current_agent}] Calling: {chunk.get('tool')}")
                elif chunk.get("type") == "ask_user":
                    asked_user = True
                    question = chunk.get("question", "")
                    print(f"\n[HITL INTERVENTION] Agent '{current_agent}' is blocked and needs your input.")
                    print(f"Question: {question}")
        except Exception as e:
            print(f"[{current_agent}] Error: {e}")
            break
            
        if asked_user:
            # Block and wait for user input from the terminal
            user_response = input("Your answer: ")
            history.append({"role": "assistant", "content": f"I called ask_user with question: {question}"})
            history.append({"role": "user", "content": f"<tool_result>\\nAction: ask_user\\nResult: {user_response}\\n</tool_result>\\n\\nContinue."})
            prompt = "" # Resume with empty prompt but updated history
        elif next_agent:
            current_agent = next_agent
            prompt = f"Delegated task: {task_str}"
            delegation_chain.append(next_agent)
            history = []
        else:
            print("\n=== Session Complete ===")
            break

if __name__ == "__main__":
    asyncio.run(main())
