# swarm_os/kernel/brain.py
"""
Brain — calls Horseshoe Swarm using genome parameters.
SWARM_URL reads from settings.
record_fitness() removed — selection.py calls it with the real score.
Calling it here with 0.0 doubled evaluations and halved average_fitness.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List

import httpx

from swarm_os.config.settings import settings

log       = logging.getLogger(__name__)
SWARM_URL = f"{settings.swarm_url}/v1/chat/completions"

TOOL_SCHEMAS: Dict[str, dict] = {
    "web_search": {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    "playwright": {
        "type": "function",
        "function": {
            "name": "playwright_browse",
            "description": "Open a URL and extract page content using browser automation",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    "filesystem": {
        "type": "function",
        "function": {
            "name": "filesystem_read",
            "description": "Read a file from the local filesystem",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    "context7": {
        "type": "function",
        "function": {
            "name": "context7_lookup",
            "description": "Look up library or framework documentation",
            "parameters": {
                "type": "object",
                "properties": {
                    "library": {"type": "string"},
                    "query":   {"type": "string"},
                },
                "required": ["library", "query"],
            },
        },
    },
    "qdrant_recall": {
        "type": "function",
        "function": {
            "name": "qdrant_recall",
            "description": "Search long-term memory for relevant past context",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "collection": {
                        "type": "string",
                        "enum": ["chat_archive", "jobs", "files", "sessions"],
                    },
                },
                "required": ["query"],
            },
        },
    },
    "code_exec": {
        "type": "function",
        "function": {
            "name": "code_exec",
            "description": "Extract, validate, and optionally run a code block",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string"},
                    "code":     {"type": "string"},
                },
                "required": ["language", "code"],
            },
        },
    },
}


def _build_system_prompt(genome, task_domain: str) -> str:
    cog   = genome.cognition
    lines: List[str] = []

    lines.append({
        "coding":   "You are a precise software engineer. Prioritize correct, runnable code.",
        "research": "You are a rigorous research analyst. Prioritize accuracy and source quality.",
        "upwork":   "You are a job market analyst. Extract structured data: budget, skills, client rating, fit score.",
        "general":  "You are a capable AI assistant.",
    }.get(task_domain, "You are a capable AI assistant."))

    if cog.decomposition_bias > 0.65:
        max_sub = max(2, int(cog.max_subtasks * 6))
        lines.append(
            f"Break complex tasks into at most {max_sub} subtasks. "
            f"Address each subtask explicitly before synthesizing your answer."
        )
    elif cog.decomposition_bias < 0.35:
        lines.append("Respond directly without decomposing into subtasks.")

    depth = genome.reasoning_depth
    if depth > 0.75:
        lines.append("Think step by step. Show your full reasoning chain before your final answer.")
    elif depth > 0.5:
        lines.append("Consider the problem carefully. Brief reasoning, then answer.")
    else:
        lines.append("Answer directly and concisely. Skip preamble.")

    if cog.self_critique_bias > 0.7:
        lines.append("After forming your answer, critique it: identify one weakness or assumption, then refine if needed.")
    if cog.reflection_depth > 0.7:
        lines.append("Before finalizing, reflect: does this fully address the question? If not, revise.")
    if cog.verification_bias > 0.7:
        lines.append("Verify key facts or logic steps before including them. Flag anything you are uncertain about.")
    if cog.hallucination_sensitivity > 0.7:
        lines.append("Do not invent facts, APIs, library names, or URLs. If you are unsure, say so explicitly.")
    if cog.retry_aggression > 0.7:
        lines.append("If a tool call fails or returns empty results, try an alternative approach before giving up.")
    if cog.summarization_bias > 0.7:
        lines.append("End your response with a concise summary of key points.")
    if genome.verbosity > 0.75:
        lines.append("Provide thorough, detailed responses.")
    elif genome.verbosity < 0.35:
        lines.append("Be brief. Use the minimum words needed. No filler.")
    if cog.parallel_tool_calls > 0.7:
        lines.append("When multiple tools are relevant, call them together rather than sequentially.")

    ctx_tokens = int(512 + genome.context_budget * 3584)
    lines.append(f"Limit your context window usage to approximately {ctx_tokens} tokens.")

    return "\n".join(lines)


def _build_user_message(genome, task: str, memory_context: str = "") -> str:
    parts = []
    if memory_context and genome.cognition.memory_read_bias > 0.5:
        parts.append(f"[Relevant context from memory]\n{memory_context}\n")
    parts.append(task or "Awaiting task.")
    return "\n".join(parts)


def make_swarm_brain(genome, task_domain: str = "general") -> Callable:
    def brain(context: Dict[str, Any]) -> Dict[str, Any]:
        org_id  = context.get("id", "unknown")
        task    = context.get("task", context.get("env", {}).get("task", ""))
        mem_ctx = context.get("memory_context", "")

        model        = genome.model
        active_tools = genome.active_tools()
        tools        = [TOOL_SCHEMAS[t] for t in active_tools if t in TOOL_SCHEMAS]
        top_k        = max(3, int(genome.retrieval_top_k * 20))

        system_prompt = _build_system_prompt(genome, task_domain)
        user_message  = _build_user_message(genome, task, mem_ctx)

        payload: Dict[str, Any] = {
            "model":       model,
            "messages":    [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            "temperature": genome.actual_temperature,
            "stream":      False,
        }
        if tools:
            payload["tools"]       = tools
            payload["tool_choice"] = "auto"

        log.debug("brain call org=%s model=%s tools=%s temp=%.2f",
                  org_id, model, active_tools, genome.actual_temperature)

        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=settings.swarm_timeout) as client:
                resp = client.post(SWARM_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()

            elapsed      = time.perf_counter() - t0
            choice       = data["choices"][0]
            content      = choice["message"].get("content", "")
            usage        = data.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)

            log.debug("brain done org=%s elapsed=%.2fs tokens=%d finish=%s",
                      org_id, elapsed, total_tokens, choice.get("finish_reason"))

            # NOTE: genome.record_fitness() is NOT called here.
            # selection.py calls it once with the real composite score.
            # Calling it here with 0.0 doubled evaluations and corrupted average_fitness.

            return {
                "content":         content,
                "model":           model,
                "tools_used":      active_tools,
                "elapsed":         elapsed,
                "total_tokens":    total_tokens,
                "prompt_tokens":   usage.get("prompt_tokens", 0),
                "finish_reason":   choice.get("finish_reason", ""),
                "tool_calls":      choice["message"].get("tool_calls", []),
                "cost":            total_tokens / 1000.0,
                "retrieval_top_k": top_k,
                "system_prompt_len": len(system_prompt),
            }

        except httpx.TimeoutException:
            log.warning("brain timeout org=%s model=%s", org_id, model)
            return {
                "error": "timeout", "cost": 5.0,
                "elapsed": float(settings.swarm_timeout),
                "content": "", "model": model, "tools_used": active_tools,
                "finish_reason": "timeout",
            }

        except Exception as e:
            log.exception("brain error org=%s", org_id)
            return {
                "error": str(e), "cost": 5.0,
                "elapsed": 0.0, "content": "",
                "model": model, "tools_used": active_tools,
                "finish_reason": "error",
            }

    return brain


class BrainRegistry:
    def __init__(self):
        self._factories: Dict[str, Callable] = {}
        self.register("swarm", make_swarm_brain)

    def register(self, name: str, factory: Callable) -> None:
        self._factories[name] = factory
        log.debug("registered brain: %s", name)

    def make(self, name: str, genome, task_domain: str = "general") -> Callable:
        if name not in self._factories:
            raise KeyError(f"Unknown brain: {name!r}. Available: {list(self._factories)}")
        return self._factories[name](genome, task_domain)


registry = BrainRegistry()


