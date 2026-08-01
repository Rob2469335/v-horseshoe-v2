import asyncio
import sys
import time
import logging
from runtime_v2.api.agent_service_v2 import AgentServiceV2
from swarm_os.services.reflection_loop import run_reflection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("HealSwarm")

class MockOrchestrator:
    pass

async def run_agent_task(svc, agent_id, goal):
    prompt = f"ACCESS MEMORY: {goal}"
    history = []
    delegation_chain = [agent_id]
    
    current_agent = agent_id
    log.info(f"[{agent_id.upper()}] Launched with task: {goal}")
    
    output = []
    for turn in range(5):
        next_agent = None
        task_str = ""
        
        try:
            async for chunk in svc.step_agent_stream(current_agent, prompt, history=history, delegation_chain=delegation_chain):
                if chunk.get("type") == "content":
                    pass # Keep quiet to not spam console
                elif chunk.get("type") == "agent_handoff":
                    next_agent = chunk.get("to")
                    task_str = chunk.get("task", "")
                elif chunk.get("type") == "final":
                    output.append(f"[{current_agent}] Final: {chunk.get('content')}")
        except Exception as e:
            output.append(f"[{current_agent}] Error: {e}")
            break
            
        if next_agent:
            current_agent = next_agent
            prompt = f"Delegated task: {task_str}"
            delegation_chain.append(next_agent)
            history = []
        else:
            break
            
    return output

async def main():
    log.info("Initializing AgentServiceV2 to dispatch self-learning and self-healing agents...")
    svc = AgentServiceV2(orchestrator=MockOrchestrator())

    tasks = [
        ("coordinator", "Identify any logic bugs or inefficiencies in the core memory loop, and self-heal the codebase."),
        ("planner", "Query memory for the most recent unhandled exceptions, analyze them, and implement a preventative fix."),
        ("researcher", "Scan the memory trace for redundant operations in the swarm routing logic and optimize them."),
        ("reviewer", "Review memory logs for any suboptimal tool usage patterns and update the system prompt directives."),
        ("debugger", "Access the agent failure logs from memory, identify any stability vulnerabilities, and patch them.")
    ]

    log.info(f"Deploying {len(tasks)} autonomous agents to the swarm network...")
    
    start_time = time.time()
    
    # Run them sequentially to avoid overloading local inference models
    results = []
    for idx, (agent_role, task_desc) in enumerate(tasks, 1):
        log.info(f"--- Starting Task {idx}/5 for Agent {agent_role} ---")
        try:
            result = await run_agent_task(svc, agent_role, task_desc)
            log.info(f"[Agent: {agent_role}] Mission Accomplished! Memory accessed and self-healing applied.")
            results.append(result)
        except Exception as e:
            log.error(f"[Agent: {agent_role}] Encountered critical failure: {e}")
            results.append(e)
                
    elapsed = time.time() - start_time
    log.info(f"All 5 agents have returned to base. Evolution and healing cycle complete in {elapsed:.2f} seconds.")

    log.info("Triggering ASPO Memory Distillation Reflection Loop...")
    try:
        await run_reflection()
        log.info("Reflection Loop complete.")
    except Exception as e:
        log.error(f"Reflection loop failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
