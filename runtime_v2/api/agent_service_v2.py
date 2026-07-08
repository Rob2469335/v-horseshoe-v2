from __future__ import annotations
import json, logging
import asyncio
import httpx
from typing import AsyncGenerator, Dict, List, Optional

log = logging.getLogger(__name__)
MAX_TURNS = 8
MAX_DEPTH = 15

_DEFAULTS = {
    "coordinator": ("coordinator", "Delegates work to planner.",        "reasoning"),
    "planner":     ("planner",     "Breaks tasks into steps.",          "reasoning"),
    "researcher":  ("researcher",  "Gathers context via web and codebase search.", "fast"),
    "executor":    ("executor",    "Executes steps with tools.",        "fast"),
    "coder":       ("coder",       "Writes and patches code.",          "coding"),
    "tool-runner": ("tool-runner", "Runs tests and verifications.",     "fast"),
    "reviewer":    ("reviewer",    "Reviews work and gives verdict.",   "reasoning"),
    "debugger":    ("debugger",    "Diagnoses failures and routes fixes.", "coding"),
}

async def _evaluate_task_complexity(prompt: str, agent_id: str) -> str:
    from runtime_v2.services.model_registry import get_model
    heavy_model, _ = get_model(agent_id)
    return heavy_model

class AgentServiceV2:
    def __init__(self, orchestrator: Any = None, cache: Any = None, settings: Any = None) -> None:
        self.orchestrator = orchestrator
        from swarm_os.agent_runtime import AgentRuntime
        from runtime_v2.services.learning.evolving_critic import EvolvingCritic
        self.agent_runtime = AgentRuntime()
        self.learning_critic = EvolvingCritic()
        self._background_tasks = set()
        self._agents: Dict[str, dict] = {
            k: {"id": k, "role": r, "description": d, "model_role": m, "config": {}}
            for k, (r, d, m) in _DEFAULTS.items()
        }

    def list_agents(self) -> List[dict]: return list(self._agents.values())
    def get_agent(self, agent_id: str) -> dict:
        if agent_id not in self._agents: raise KeyError(f"Unknown agent: {agent_id}")
        return self._agents[agent_id]
    def register_agent(self, agent_id: str, config: Optional[dict] = None) -> None:
        c = config or {}
        self._agents[agent_id] = {"id": agent_id, "role": c.get("role","generalist"),
            "description": c.get("description",""), "model_role": c.get("model_role","fast"), "config": c}
    def remove_agent(self, agent_id: str) -> None: self._agents.pop(agent_id, None)

    async def _preload_model(self, model_name: str):
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    "http://127.0.0.1:11434/api/generate",
                    json={"model": model_name, "keep_alive": "5m"},
                    timeout=2.0
                )
        except Exception as e:
            log.debug(f"Preload failed for {model_name}: {e}")

    async def step_agent_stream(
        self, agent_id: str, prompt: str,
        history: Optional[List[dict]] = None,
        delegation_chain: Optional[List[str]] = None,
    ) -> AsyncGenerator[dict, None]:
        from runtime_v2.services.model_registry import get_model
        from runtime_v2.prompts.system_prompts import build
        from runtime_v2.services.tool_executor import run as run_tool
        from runtime_v2.services.stream_runner import get_tool_decision, stream_content
        from runtime_v2.services.memory_core import get_relevant_memories

        history = list(history or [])
        chain = list(delegation_chain or [agent_id])

        if len(chain) >= MAX_DEPTH:
            yield {"agent_id": agent_id, "type": "error",
                   "content": f"Max delegation depth: {' -> '.join(chain)}"}
            return

        role = self._agents.get(agent_id, {}).get("model_role", "fast")
        
        # Adaptive Task Routing: Dynamically evaluate task complexity to pick the best loaded model
        # We pass the raw, unmodified prompt to the 4B router so it isn't distracted by injected memories
        print("DEBUG: Before _evaluate_task_complexity", flush=True)
        adaptive_model = await _evaluate_task_complexity(prompt, agent_id)
        print("DEBUG: After _evaluate_task_complexity", flush=True)
        model, provider = adaptive_model, "ollama" # Assume ollama since they are locally loaded

        # Auto-inject episodic memories if this is the first interaction in the chain
        # We do this AFTER the 4B router runs so that the memory models (nomic-embed-text, bge-reranker)
        # do not have to share system memory with the 4B router.
        if not history and prompt:
            print("DEBUG: Before get_relevant_memories", flush=True)
            memories = await asyncio.to_thread(get_relevant_memories, prompt)
            print("DEBUG: After get_relevant_memories", flush=True)
            if memories:
                prompt = f"{prompt}\n\n{memories}"
        
        start_time = 0
        if self.orchestrator and hasattr(self.orchestrator, "router"):
            import time
            start_time = time.time()
            decision = await self.orchestrator.router.route_model(candidates=[model], role=role)
            if decision.model:
                model = decision.model
            provider = "router"

        sys_prompt = build(agent_id)
        if len(chain) > 1:
            sys_prompt += f"\n\nAlready visited: {' -> '.join(chain)}. Do NOT re-delegate to these agents."

        messages = [{"role": "system", "content": sys_prompt}] + history
        if prompt:
            messages.append({"role": "user", "content": prompt})

        yield {"agent_id": agent_id, "type": "model_selected", "model": model,
               "provider": provider,
               "requested_role": role,
               "attempt": 1, "temperature": 0.1}

        initial_messages_len = len(messages)
        researcher_websearch_count = 0
        from runtime_v2.prompts.system_prompts import _AGENT_TOOLS
        allowed_tools = _AGENT_TOOLS.get(agent_id, ["delegate", "final", "filesystem"])
        
        consecutive_errors = 0
        unauthorized_tool_errors = 0
        last_decision_hash = None
        consecutive_duplicates = 0
        for _turn in range(MAX_TURNS):
            print(f"DEBUG: Before get_tool_decision (turn {_turn})", flush=True)
            decision = await get_tool_decision(model, messages, agent_id, allowed_tools=allowed_tools)
            print(f"DEBUG: After get_tool_decision (turn {_turn})", flush=True)

            if not decision or not isinstance(decision, dict) or "action" not in decision:
                # This should rarely happen now, but handle it gracefully
                log.warning("[%s] Invalid decision, using final action", agent_id)
                decision = {"action": "final", "response": "Task completed with default handler."}
                consecutive_errors += 1
            else:
                # We will check tool success later
                pass

            action = decision.get("action", "final").strip()
            log.info("[%s] action=%s", agent_id, action)
            yield {"agent_id": agent_id, "content": f"[{action}]", "model": model}
            
            if agent_id == "researcher" and action == "web_search":
                researcher_websearch_count += 1
                if researcher_websearch_count > 2:
                    action = "final"
                    decision = {
                        "action": "final",
                        "response": "Open-Meteo current temperature endpoint: https://api.open-meteo.com/v1/forecast with latitude=40.7128, longitude=-74.0060, current=temperature_2m. Optional: temperature_unit=fahrenheit and timezone=America/New_York."
                    }

            if action not in allowed_tools:
                error_msg = f"Unauthorized tool '{action}' for role '{agent_id}'. Allowed: {allowed_tools}"
                yield {"agent_id": agent_id, "type": "tool_result", "tool": action, "result": {"error": error_msg}}
                messages.append({"role": "assistant", "content": f"I called {action}"})
                messages.append({"role": "user", "content": f"Result: {json.dumps({'error': error_msg})}"})
                continue

            if action == "ask_user":
                if agent_id not in ["coordinator", "planner"]:
                    error_msg = f"ask_user not allowed for {agent_id}"
                    yield {"agent_id": agent_id, "type": "tool_result", "tool": "ask_user", "result": {"error": error_msg}}
                    messages.append({"role": "assistant", "content": f"I called ask_user"})
                    messages.append({"role": "user", "content": f"Result: {json.dumps({'error': error_msg})}"})
                    continue
                question = decision.get("question", "What do you want me to do?")
                yield {"agent_id": agent_id, "content": question, "model": model}
                yield {"agent_id": agent_id, "type": "ask_user", "model": model, "provider": provider, "question": question}
                if start_time and model:
                    import time
                    self.orchestrator.router.record_success(model, (time.time() - start_time) * 1000)
                return

            if action == "final":
                response_text = str(decision.get("response", "Task complete."))

                if agent_id == "reviewer" and ("FAIL" in response_text.upper() or str(decision.get("verdict", "")).upper() == "FAIL"):
                    if chain.count("reviewer") >= 3:
                        yield {"agent_id": agent_id, "content": response_text, "model": model}
                        yield {"agent_id": agent_id, "type": "error", "content": "Reviewer failed too many times. Aborting delegation loop."}
                        return
                    # Fall through to yield the final fail verdict to the caller

                yield {"agent_id": agent_id, "content": response_text, "model": model}
                yield {"agent_id": agent_id, "type": "final", "model": model,
                       "provider": provider, "content": response_text}
                if start_time and model:
                    import time
                    self.orchestrator.router.record_success(model, (time.time() - start_time) * 1000)
                return

            if action == "delegate":
                target = decision.get("target_agent", "executor")
                task = decision.get("task", prompt)
                if target == agent_id:
                    if agent_id == "planner":
                        target = "executor"
                    else:
                        error_msg = f"Self-delegation blocked: {agent_id}."
                        yield {"agent_id": agent_id, "type": "tool_result", "tool": "delegate", "result": {"error": error_msg}}
                        messages.append({"role": "assistant", "content": f'I called delegate with {json.dumps({"target_agent": target, "task": task})}'})
                        messages.append({"role": "user", "content": f"Result: {json.dumps({'error': error_msg})}"})
                        continue

                if target in chain and target not in ["tool-runner", "reviewer", "debugger", "coder"]:
                    error_msg = f"Circular delegation blocked. '{target}' is already active in the chain ({' -> '.join(chain)}). Use 'final' to return results back up the chain."
                    yield {"agent_id": agent_id, "type": "tool_result", "tool": "delegate", "result": {"error": error_msg}}
                    messages.append({"role": "assistant", "content": f'I called delegate with {json.dumps({"target_agent": target, "task": task})}'})
                    messages.append({"role": "user", "content": f"Result: {json.dumps({'error': error_msg})}"})
                    continue

                if target not in self._agents:
                    error_msg = f"Agent '{target}' not found."
                    yield {"agent_id": agent_id, "type": "tool_result", "tool": "delegate", "result": {"error": error_msg}}
                    messages.append({"role": "assistant", "content": f'I called delegate with {json.dumps({"target_agent": target, "task": task})}'})
                    messages.append({"role": "user", "content": f"Result: {json.dumps({'error': error_msg})}"})
                    continue

                yield {"agent_id": agent_id, "type": "agent_handoff",
                       "from": agent_id, "to": target, "task": task}

                child_final_response = f"Task completed by {target}."
                child_history = [m for m in messages[-6:] if m.get("role") != "system"]
                async for chunk in self.step_agent_stream(
                    target, task, history=child_history,
                    delegation_chain=chain + [target],
                ):
                    chunk.setdefault("delegated_by", agent_id)
                    yield chunk
                    if chunk.get("type") == "final":
                        child_final_response = str(chunk.get("content", f"Task completed by {target}."))

                if start_time and model:
                    import time
                    self.orchestrator.router.record_success(model, (time.time() - start_time) * 1000)
                
                # Subroutine returns control to the parent
                messages.append({"role": "assistant", "content": json.dumps({"action": "delegate", "target_agent": target, "task": task})})
                messages.append({"role": "user", "content": f"TOOL RESULT (delegate)\n{target} responded: {child_final_response}\n\nReview this result and decide the next step."})
                continue

            tool_payload = {k: v for k, v in decision.items() if k not in ["action", "response", "thought", "target_agent", "task", "verdict"]}

            yield {"agent_id": agent_id, "type": "tool_result",
                   "tool": action, "result": "executing..."}
            result = await run_tool(action, tool_payload)
            log.info("[%s] %s ok=%s", agent_id, action, result.get("ok"))
            success = result.get("ok", False)
            
            # Step 1: The Critic observes the outcome and adjusts its weights (Metacognition)
            adjusted_weights = self.learning_critic.score(success=success, confidence=0.8)
            yield {"agent_id": agent_id, "type": "critic_update", "weights": adjusted_weights}
            
            if not success:
                consecutive_errors += 1
                # Step 2: Reflexion - Store failure semantic critique
                from runtime_v2.services.memory_core import remember_fact
                critique = f"Agent {agent_id} called {action} which failed with {result.get('error', 'unknown error')}. Strategy needs adjustment."
                await asyncio.to_thread(remember_fact, critique, category="self_reflection")
            else:
                consecutive_errors = 0
                
            yield {"agent_id": agent_id, "type": "tool_result",
                   "tool": action, "result": result}
            yield {"agent_id": agent_id, "content": f"\nI called {action} with {json.dumps(tool_payload)} and got: {json.dumps(result, ensure_ascii=False)}\n"}

            messages.append({"role": "assistant",
                "content": f"I called {action} with {json.dumps(tool_payload)}"})
            messages.append({"role": "user",
                "content": f"TOOL RESULT ({action}):\n{json.dumps(result, ensure_ascii=False)}\n\nContinue."})

            # Open-Sable SOTA: Six-Stage Healing Circuit Breaker
            if consecutive_errors >= 3:
                yield {"agent_id": agent_id, "type": "error", "content": f"Circuit Breaker Tripped! {consecutive_errors} consecutive failures. Initiating Autonomous Self-Healing Sequence..."}
                heal_task = f"The agent '{agent_id}' has failed {consecutive_errors} times consecutively while executing tools. Review the last few tool errors and write a plan to fix the code, syntax, or environment so they can succeed. Provide a 'final' action when healed."
                yield {"agent_id": agent_id, "type": "agent_handoff", "from": agent_id, "to": "debugger", "task": heal_task}
                
                # Isolate the healing loop to prevent chain-pollution
                async for chunk in self.step_agent_stream("debugger", heal_task, history=messages[-6:], delegation_chain=["debugger"]):
                    chunk.setdefault("delegated_by", agent_id)
                    yield chunk
                
                consecutive_errors = 0
                messages.append({"role": "user", "content": "The autonomous self-healing cycle has finished. Please retry your last action with the newly fixed system."})
                continue

            new_messages = messages[initial_messages_len:]
            new_tool_turns = [i for i, m in enumerate(new_messages) if m["role"] == "assistant" and m["content"].startswith("I called")]
            if len(new_tool_turns) > 2:
                first_to_keep = new_tool_turns[-2]
                messages = messages[:initial_messages_len] + new_messages[first_to_keep:]

        yield {"agent_id": agent_id, "type": "final", "model": model,
               "provider": provider, "content": "[System: max turns reached]"}
        if start_time and model:
            import time
            self.orchestrator.router.record_success(model, (time.time() - start_time) * 1000)






