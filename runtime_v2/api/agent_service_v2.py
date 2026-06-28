from __future__ import annotations
import json, logging
from typing import AsyncGenerator, Dict, List, Optional

log = logging.getLogger(__name__)
MAX_TURNS = 8
MAX_DEPTH = 7

_DEFAULTS = {
    "coordinator": ("coordinator", "Delegates work to planner.",        "reasoning"),
    "planner":     ("planner",     "Breaks tasks into steps.",          "reasoning"),
    "executor":    ("executor",    "Executes steps with tools.",        "fast"),
    "coder":       ("coder",       "Writes and patches code.",          "coding"),
    "tool-runner": ("tool-runner", "Runs tests and verifications.",     "fast"),
    "reviewer":    ("reviewer",    "Reviews work and gives verdict.",   "reasoning"),
    "debugger":    ("debugger",    "Diagnoses failures and routes fixes.", "coding"),
}

class AgentServiceV2:
    def __init__(self, orchestrator=None, cache=None, settings=None):
        self.orchestrator = orchestrator
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

    async def step_agent_stream(
        self, agent_id: str, prompt: str,
        history: Optional[List[dict]] = None,
        delegation_chain: Optional[List[str]] = None,
    ) -> AsyncGenerator[dict, None]:
        from runtime_v2.services.model_registry import get_model
        from runtime_v2.prompts.system_prompts import build
        from runtime_v2.services.tool_executor import run as run_tool
        from runtime_v2.services.stream_runner import get_tool_decision, stream_content

        history = list(history or [])
        chain = list(delegation_chain or [agent_id])

        if len(chain) >= MAX_DEPTH:
            yield {"agent_id": agent_id, "type": "error",
                   "content": f"Max delegation depth: {' -> '.join(chain)}"}
            return

        model, provider = get_model(agent_id)
        messages = [{"role": "system", "content": build(agent_id)}] + history
        if prompt:
            messages.append({"role": "user", "content": prompt})

        yield {"agent_id": agent_id, "type": "model_selected", "model": model,
               "provider": provider,
               "requested_role": self._agents.get(agent_id, {}).get("model_role", "fast"),
               "attempt": 1, "temperature": 0.1}

        if agent_id == "reviewer":
            full_text = ""
            async for piece, kind in stream_content(model, messages, agent_id):
                if kind == "error":
                    yield {"agent_id": agent_id, "type": "error", "content": piece}
                    return
                full_text += piece
                yield {"agent_id": agent_id, "content": piece, "model": model}
            yield {"agent_id": agent_id, "type": "final", "model": model,
                   "provider": provider, "content": full_text}
            return

        for _turn in range(MAX_TURNS):
            decision = await get_tool_decision(model, messages, agent_id)
            if not decision:
                yield {"agent_id": agent_id, "type": "error",
                       "content": "Failed to get tool decision from model"}
                return

            action = decision.get("action", "final")
            log.info("[%s] turn %d action=%s", agent_id, _turn, action)
            yield {"agent_id": agent_id, "content": f"[{action}]", "model": model}

            if action == "ask_user" and agent_id != "coordinator":
                yield {"agent_id": agent_id, "type": "final", "model": model,
                       "provider": provider,
                       "content": "Only the coordinator agent is allowed to ask the user questions."}
                return

            if action == "final":
                response_text = decision.get("response", "Task complete.")
                yield {"agent_id": agent_id, "content": response_text, "model": model}
                yield {"agent_id": agent_id, "type": "final", "model": model,
                       "provider": provider, "content": response_text}
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
                if agent_id == "executor" and target.lower().strip() in ["planner", "coordinator"]:
                    target = "coder"
                if agent_id == "coder" and target.lower().strip() in ["executor", "planner"]:
                    target = "tool-runner"
                if agent_id == "tool-runner" and target.lower().strip() in ["coder", "executor"]:
                    target = "reviewer"
                
                if target in chain:
                    error_msg = f"Circular delegation: {' -> '.join(chain)} -> {target}."
                    print(f"DEBUG CIRCULAR: agent={agent_id}, target={target}, chain={chain}")
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

                async for chunk in self.step_agent_stream(
                    target, task, history=list(messages),
                    delegation_chain=chain + [target],
                ):
                    chunk.setdefault("delegated_by", agent_id)
                    yield chunk
                return

            tool_payload = {}
            if action == "web_search":
                tool_payload = {"query": decision.get("query", "")}
            elif action == "filesystem":
                tool_payload = {k: decision[k] for k in
                    ["operation","path","content","old","new","pattern"] if k in decision}
            elif action == "sandbox_repl":
                tool_payload = {k: decision[k] for k in
                    ["language","code","command","path"] if k in decision}
            elif action == "vscode_automation":
                tool_payload = {"command": decision.get("command",""), "args": decision.get("args",[])}

            yield {"agent_id": agent_id, "type": "tool_result",
                   "tool": action, "result": "executing..."}
            result = await run_tool(action, tool_payload)
            log.info("[%s] %s ok=%s", agent_id, action, result.get("ok"))
            yield {"agent_id": agent_id, "type": "tool_result",
                   "tool": action, "result": result}

            messages.append({"role": "assistant",
                "content": f"I called {action} with {json.dumps(tool_payload)}"})
            messages.append({"role": "user",
                "content": f"TOOL RESULT ({action}):\n{json.dumps(result, ensure_ascii=False)}\n\nContinue."})

        yield {"agent_id": agent_id, "type": "final", "model": model,
               "provider": provider, "content": "[System: max turns reached]"}

