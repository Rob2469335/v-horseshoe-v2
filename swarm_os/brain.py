# swarm_os/kernel/brain.py
"""
Brain — uses injected in-process generation via orchestrator.
record_fitness() removed — selection.py calls it with the real score.
Calling it here with 0.0 doubled evaluations and halved average_fitness.
"""
from __future__ import annotations

import logging
import time
import json
import random
from typing import Any, Callable, Dict, List
import httpx
import os



log       = logging.getLogger(__name__)

SWARM_URL = os.getenv("SWARM_URL", "http://127.0.0.1:8000/generate")

def _safe_attr(obj, name: str, default):
    return getattr(obj, name, default)

def _safe_model(genome) -> str:
    value = getattr(genome, "model", None)
    return value if isinstance(value, str) and value.strip() else "qwen3.5-9b"

def _safe_temperature(genome) -> float:
    return float(getattr(genome, "actual_temperature", 0.2))

def _safe_memory_read_bias(genome: Any) -> float:
    if not getattr(genome, "cognition", None):
        return 0.0
    return float(getattr(genome.cognition, "memory_read_bias", 0.0))


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


def _build_system_prompt(genome: Any, task_domain: str) -> str:
    cog = getattr(genome, "cognition", None)
    if not cog:
        return "You are a capable AI assistant."

    lines: List[str] = []

    lines.append({
        "coding":   "You are a precise software engineer. Prioritize correct, runnable code. CRITICAL: Use tools to read the codebase BEFORE writing any files. Do NOT hallucinate reports or files.",
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

    if getattr(cog, "self_critique_bias", 0.0) > 0.7:
        lines.append("After forming your answer, critique it: identify one weakness or assumption, then refine if needed.")
    if getattr(cog, "reflection_depth", 0.0) > 0.7:
        lines.append("Before finalizing, reflect: does this fully address the question? If not, revise.")
    if getattr(cog, "verification_bias", 0.0) > 0.7:
        lines.append("Verify key facts or logic steps before including them. Flag anything you are uncertain about.")
    if getattr(cog, "hallucination_sensitivity", 0.0) > 0.7:
        lines.append("Do not invent facts, APIs, library names, or URLs. If you are unsure, say so explicitly.")
    if getattr(cog, "retry_aggression", 0.0) > 0.7:
        lines.append("If a tool call fails or returns empty results, try an alternative approach before giving up.")
    if getattr(cog, "summarization_bias", 0.0) > 0.7:
        lines.append("End your response with a concise summary of key points.")
    if genome.verbosity > 0.75:
        lines.append("Provide thorough, detailed responses.")
    elif genome.verbosity < 0.35:
        lines.append("Be brief. Use the minimum words needed. No filler.")
    if getattr(cog, "parallel_tool_calls", 0.0) > 0.7:
        lines.append("When multiple tools are relevant, call them together rather than sequentially.")

    ctx_tokens = int(512 + genome.context_budget * 3584)
    lines.append(f"Limit your context window usage to approximately {ctx_tokens} tokens.")

    return "\n".join(lines)


def _build_user_message(genome, task: str, memory_context: str = "") -> str:
    parts = []
    if memory_context and _safe_memory_read_bias(genome) > 0.5:
        parts.append(f"[Relevant context from memory]\n{memory_context}\n")
    parts.append(task or "Awaiting task.")
    return "\n".join(parts)


from typing import Optional

def _call_generate_fn_brain(
    genome: Any,
    requested_model: Optional[str],
    system_prompt: str,
    user_message: str,
    top_k: int,
    active_tools: List[str],
    generate_fn: Callable,
    t0: float,
) -> Dict[str, Any]:
    prompt = f"{system_prompt}\n\n{user_message}"
    phenotype = None
    if hasattr(genome, "build_phenotype"):
        built = genome.build_phenotype()
        phenotype = built.__dict__ if hasattr(built, "__dict__") else built
    content, resolved_model = generate_fn(requested_model, prompt, phenotype=phenotype)
    elapsed = time.perf_counter() - t0
    return {
        "content": content,
        "model": resolved_model or requested_model or _safe_model(genome),
        "tools_used": active_tools,
        "elapsed": elapsed,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "finish_reason": "stop",
        "tool_calls": [],
        "cost": 0.0,
        "retrieval_top_k": top_k,
        "system_prompt_len": len(system_prompt),
    }

def make_swarm_brain(genome: Any, task_domain: str = "general", generate_fn: Optional[Callable] = None) -> Callable:
    def brain(context: Dict[str, Any]) -> Dict[str, Any]:
        org_id  = context.get("id", "unknown")
        task    = context.get("task", context.get("env", {}).get("task", ""))
        mem_ctx = context.get("memory_context", "")

        requested_model = getattr(genome, "model", None)
        active_tools = genome.active_tools() if hasattr(genome, "active_tools") else []
        top_k        = max(3, int(_safe_attr(genome, "retrieval_top_k", 0.25) * 20))

        system_prompt = _build_system_prompt(genome, task_domain)
        user_message  = _build_user_message(genome, task, mem_ctx)
        
        try:
            from swarm_os.services.reflection_loop import get_reflection_service
            import asyncio
            warning = asyncio.run(get_reflection_service().check_for_past_mistakes(task))
            if warning:
                system_prompt += f"\n\n[CRITICAL AVOIDANCE MEMORY]\n{warning}"
        except Exception as e:
            log.warning("Reflexion check failed: %s", e)

        payload: Dict[str, Any] = {
            "model": requested_model or _safe_model(genome),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": _safe_temperature(genome),
            "stream": False,
        }

        t0 = time.perf_counter()
        try:
            if generate_fn is not None:
                return _call_generate_fn_brain(genome, requested_model, system_prompt, user_message, top_k, active_tools, generate_fn, t0)
            from swarm_os.services.llm_client import SwarmBrainClient
            client = SwarmBrainClient(swarm_url=SWARM_URL)
            return client.generate(
                org_id=org_id,
                requested_model=requested_model,
                default_model=_safe_model(genome),
                payload=payload,
                top_k=top_k,
                active_tools=active_tools,
                system_prompt_len=len(system_prompt),
                timeout_budget=float(_safe_attr(genome, "timeout_budget", 300.0)),
            )
        except httpx.TimeoutException:
            return {
                "error": "timeout", "cost": 5.0, "elapsed": _safe_attr(genome, "timeout_budget", 300.0), "content": "",
                "model": requested_model or _safe_model(genome), "tools_used": active_tools, "finish_reason": "timeout",
            }
        except Exception as e:
            return {
                "error": str(e), "cost": 5.0, "elapsed": 0.0, "content": "",
                "model": requested_model or _safe_model(genome), "tools_used": active_tools, "finish_reason": "error",
            }

    return brain



class BrainRegistry:
    """Registry for AI brain generation factories."""
    def __init__(self):
        self._factories: Dict[str, Callable] = {}
        self.register("swarm", make_swarm_brain)
        self.register("simple", make_swarm_brain)

    def register(self, name: str, factory: Callable) -> None:
        self._factories[name] = factory
        log.debug("registered brain: %s", name)

    def get(self, name: str) -> Callable:
        if name not in self._factories:
            raise KeyError(f"Unknown brain: {name!r}. Available: {list(self._factories)}")
        return self._factories[name]

    def make(self, name: str, genome, task_domain: str = "general", generate_fn=None) -> Callable:
        return self.get(name)(genome, task_domain, generate_fn=generate_fn)


registry = BrainRegistry()


def simple_brain(genome, task_domain: str = "general", generate_fn=None):
    return make_swarm_brain(genome, task_domain, generate_fn=generate_fn)

__all__ = [
    "BrainRegistry",
    "make_swarm_brain",
    "simple_brain",
    "registry",
]








