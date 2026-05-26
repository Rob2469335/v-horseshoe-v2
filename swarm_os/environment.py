# swarm_os/kernel/environment.py
"""
Environment — the world organisms live in.
Weighted random domain selection with burst mode.
No round-robin — organisms cannot overfit to a predictable schedule.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TASK_DOMAINS = ["coding", "research", "upwork"]

TASK_POOL: Dict[str, List[str]] = {
    "coding": [
        "Write a Python function that retries an async HTTP call up to 3 times with exponential backoff.",
        "Debug this error: AttributeError: 'NoneType' object has no attribute 'json'. What causes it and how do I fix it?",
        "Write a FastAPI route that accepts a JSON body, validates it with Pydantic, and returns a 422 on failure.",
        "Explain what asyncio.gather() does and when to use it instead of await.",
        "Write a PowerShell script that finds all Python files modified in the last 7 days.",
        "Refactor this function to be async: def get_data(url): return requests.get(url).json()",
        "What is the difference between __init__ and __new__ in Python? Give a practical example.",
        "Write a context manager that times a code block and logs the elapsed time.",
    ],
    "research": [
        "What are the key differences between Qdrant and Weaviate for local deployment in 2026?",
        "Summarize the main approaches to LLM fitness evaluation in evolutionary computation.",
        "What is the current state of Intel Arc graphics driver support for AI workloads on Windows?",
        "Explain how bge-reranker works and why it outperforms cosine similarity for passage reranking.",
        "What are the best practices for running Ollama models on a laptop with shared GPU memory?",
        "Compare softmax selection vs tournament selection in genetic algorithms.",
        "What makes Qwen2.5-Coder better than CodeLlama for code generation tasks?",
        "Explain the difference between episodic and semantic memory in AI agent systems.",
    ],
    "upwork": [
        "Analyze this job: 'Looking for Python developer to build FastAPI backend with Qdrant integration. Budget $500-1000. Client rating 4.8. 12 hires. Skills: Python, FastAPI, Vector DB.'",
        "Score this job for fit: 'Need AI agent developer for local LLM orchestration system. Fixed price $800. Client rating 4.6. Skills: LangChain, Ollama, Python.'",
        "Extract structured data from: 'Seeking automation expert for web scraping project using Playwright. Hourly $35-50. Client rating 4.9. 45 hires. Long term potential.'",
        "Is this job worth applying to: 'Build MCP server integrations for VS Code extension. Budget $200-400. New client, no reviews. Skills: TypeScript, MCP, VS Code API.'",
        "Rate this job 1-10: 'AI chatbot developer needed. Budget $100. Client rating 3.2. 1 hire. Looking for ChatGPT wrapper. Quick turnaround.'",
        "Analyze fit: 'Seeking FastAPI + React developer for AI dashboard. $1500-3000. Client rating 4.95. 30 hires. Long term relationship possible.'",
    ],
}

DOMAIN_WEIGHTS = {"coding": 0.4, "research": 0.35, "upwork": 0.25}
_BURST_PROBABILITY = 0.25
_MAX_BURST_LENGTH  = 3


@dataclass
class Task:
    domain:    str
    prompt:    str
    issued_at: float = field(default_factory=time.time)
    task_id:   str   = field(
        default_factory=lambda: f"task_{int(time.time()*1000) % 100000}"
    )


@dataclass
class Environment:
    resources: Dict[str, float] = field(
        default_factory=lambda: {"compute": 100.0, "energy": 100.0, "budget": 50.0}
    )
    entropy:          float = 0.0
    t:                int   = 0
    current_task:     Optional[Task] = None
    task_history:     List[Task] = field(default_factory=list)
    _burst_domain:    Optional[str] = field(default=None, repr=False)
    _burst_remaining: int = field(default=0, repr=False)

    def _select_domain(self) -> str:
        if self._burst_remaining > 0 and self._burst_domain:
            self._burst_remaining -= 1
            return self._burst_domain
        domains = list(DOMAIN_WEIGHTS.keys())
        weights = [DOMAIN_WEIGHTS[d] for d in domains]
        domain  = random.choices(domains, weights=weights, k=1)[0]
        if random.random() < _BURST_PROBABILITY:
            self._burst_domain    = domain
            self._burst_remaining = random.randint(1, _MAX_BURST_LENGTH - 1)
            log.debug("burst domain=%s length=%d", domain, self._burst_remaining + 1)
        return domain

    def tick(self) -> None:
        self.entropy += random.uniform(0.01, 0.03)
        self.resources["energy"] = max(0.0, self.resources["energy"] - self.entropy * 0.1)
        self.t += 1
        domain = self._select_domain()
        prompt = random.choice(TASK_POOL[domain])
        self.current_task = Task(domain=domain, prompt=prompt)
        self.task_history.append(self.current_task)

    def apply(self, actions: List[Dict[str, Any]]) -> None:
        for a in actions:
            cost = max(0.0, min(float(a.get("cost", 0.1)), 10.0))
            self.resources["compute"] = max(0.0, self.resources["compute"] - cost)
        self.resources["compute"] = min(100.0, self.resources["compute"] + 5.0)

    def state(self) -> Dict[str, Any]:
        return {
            "resources":    dict(self.resources),
            "entropy":      round(self.entropy, 4),
            "t":            self.t,
            "current_task": {
                "domain":  self.current_task.domain,
                "prompt":  self.current_task.prompt,
                "task_id": self.current_task.task_id,
            } if self.current_task else None,
            "burst_active": self._burst_remaining > 0,
        }

    def resource_pressure(self) -> float:
        return 1.0 - (self.resources["compute"] + self.resources["energy"]) / 200.0
