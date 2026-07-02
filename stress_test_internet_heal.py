import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

async def live_internet_test():
    # Setup real dependencies (No Mocks!)
    from swarm_os.core.settings import get_settings
    settings = get_settings()
    
    from swarm_os.services.orchestrator import Orchestrator
    orchestrator = Orchestrator()
    
    from runtime_v2.api.agent_service_v2 import AgentServiceV2
    svc = AgentServiceV2(
        orchestrator=orchestrator,
        settings=settings,
    )

    # 1. Create a broken file that requires external knowledge to fix
    broken_code = """
import urllib.request
# This API endpoint requires a special User-Agent and JSON parsing, 
# but we are doing it completely wrong and it crashes.
def fetch_weather():
    url = "https://api.weather.gov/points/39.7456,-97.0892"
    # This fails because we are omitting the User-Agent parameter
    req = urllib.request.Request(url)
    resp = urllib.request.urlopen(req)
    return resp.read()
    
if __name__ == '__main__':
    print(fetch_weather())
"""
    with open("broken_weather.py", "w") as f:
        f.write(broken_code.strip())
        
    print("Created 'broken_weather.py' with intentional 403 Forbidden flaw.")

    goal = (
        "The script 'broken_weather.py' is crashing with a HTTP 403 Forbidden error because weather.gov requires a User-Agent header. "
        "Coordinate your agents to use `web_search` to find the correct python 'requests' library syntax or urllib syntax for adding a User-Agent. "
        "Then fix the file using `filesystem`, run it with `sandbox_repl` to verify it prints JSON, and finalize the task."
    )
    
    current_agent = "coordinator"
    prompt = goal
    history = []
    delegation_chain = ["coordinator"]
    
    print(f"\n--- INTERNET SELF-HEAL GOAL: {goal} ---")
    
    for turn in range(15):
        print(f"\n[{current_agent.upper()} IS THINKING...]")
        
        next_agent = None
        task = ""
        
        try:
            async for chunk in svc.step_agent_stream(current_agent, prompt, history=history, delegation_chain=delegation_chain):
                if chunk.get("type") == "content":
                    text = chunk.get("content", "")
                    sys.stdout.buffer.write(text.encode('utf-8', errors='replace'))
                    sys.stdout.flush()
                elif chunk.get("type") == "agent_handoff":
                    next_agent = chunk.get("to")
                    task = chunk.get("task", "")
                    sys.stdout.buffer.write(f"\n\n>>> DELEGATING TO {next_agent.upper()} >>>\n".encode('utf-8'))
                    sys.stdout.buffer.write(f"Task: {task}\n".encode('utf-8'))
                    sys.stdout.flush()
                elif chunk.get("type") == "tool_result":
                    tool = chunk.get("tool")
                    res = str(chunk.get("result", ""))[:200]
                    sys.stdout.buffer.write(f"\n[TOOL EXECUTED: {tool}] -> {res}...\n".encode('utf-8'))
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
            history = []
        else:
            print(f"[{current_agent.upper()} finished without delegating. End of chain.]")
            break

    # Verify the fix
    print("\n--- FINAL VERIFICATION ---")
    if os.path.exists("broken_weather.py"):
        with open("broken_weather.py", "r") as f:
            final_code = f.read()
        if "headers=" in final_code or "add_header" in final_code or "requests.get" in final_code:
            print("[SUCCESS] The agent successfully researched and injected the missing header!")
        else:
            print("[FAILED] The agent failed to apply the fix.")
            print(f"Final Code:\n{final_code}")
    else:
        print("[FAILED] File was deleted.")

if __name__ == "__main__":
    asyncio.run(live_internet_test())
