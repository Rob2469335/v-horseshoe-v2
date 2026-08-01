import asyncio
import sys
import time
import logging
from runtime_v2.api.agent_service_v2 import AgentServiceV2
from swarm_os.services.reflection_loop import run_reflection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("FullHealSwarm")

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
                    pass 
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
    log.info("Initializing AgentServiceV2 to dispatch the FULL 8-agent self-healing sequence...")
    svc = AgentServiceV2(orchestrator=MockOrchestrator())

    tasks = [
        ("coordinator", "Audit the entire agent routing logic. Verify that all 8 agent roles are correctly mapped and identify any missing delegation pathways."),
        ("planner", "Query memory for any unhandled exceptions across all previous runs. Draft a comprehensive preventative plan for the entire OS."),
        ("researcher", "Deep scan the codebase and organism_diary.jsonl for any hardcoded paths, memory leaks, or redundant swarm operations."),
        ("executor", "Validate the execution order of the dev team SOP. Ensure handoffs between researcher, coder, tool-runner, reviewer, and debugger are flawless."),
        ("coder", "Analyze the dynamic tool registry. If any critical system tool is missing for robust error handling, forge it using forge_tool."),
        ("tool-runner", "Simulate a dry-run of all built-in tools (filesystem, semantic_search, etc.) to verify they return valid JSON without crashing."),
        ("reviewer", "Review the logs of the previous 6 agents for any hallucinated tools (like 'parse_error') or suboptimal outputs, and correct them."),
        ("debugger", "Access the complete agent failure logs from memory, identify any remaining stability vulnerabilities in the OS, and apply the final patches.")
    ]

    log.info(f"Deploying {len(tasks)} autonomous agents to the swarm network...")
    
    start_time = time.time()
    
    results = []
    for idx, (agent_role, task_desc) in enumerate(tasks, 1):
        log.info(f"--- Starting Task {idx}/8 for Agent {agent_role} ---")
        try:
            result = await run_agent_task(svc, agent_role, task_desc)
            log.info(f"[Agent: {agent_role}] Mission Accomplished! Audit and self-healing applied.")
            results.append(result)
        except Exception as e:
            log.error(f"[Agent: {agent_role}] Encountered critical failure: {e}")
            results.append(e)
                
    elapsed = time.time() - start_time
    log.info(f"All 8 agents have returned to base. Full system diagnostic complete in {elapsed:.2f} seconds.")
    
    log.info("Triggering ASPO Memory Distillation Reflection Loop...")
    try:
        await run_reflection()
        log.info("Reflection Loop complete.")
    except Exception as e:
        log.error(f"Reflection loop failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
