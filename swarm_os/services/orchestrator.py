from __future__ import annotations

import logging
import json

import httpx

from ..core.settings import get_settings
from ..domain.models import SwarmJob, SwarmNode
from ..domain.policies import can_accept_job, score_node
from ..events.envelope import EventEnvelope
from ..events.store import EventStore
from ..infra.ollama import OllamaClient
from ..services.simulation_service import SimulationService
from ..config.settings import settings as swarm_settings

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self) -> None:
        s = get_settings()
        self.settings = s
        self.events = EventStore(s.events_dir)
        self.ollama = OllamaClient()
        self.simulation = SimulationService()
        self.swarm_base_url = swarm_settings.swarm_url
        self.swarm_timeout = swarm_settings.swarm_timeout

    def assign_job(self, node: SwarmNode, job: SwarmJob) -> bool:
        accepted = can_accept_job(node, job)
        event = EventEnvelope.create(
            event_type="job.assignment.checked",
            source="orchestrator",
            payload={
                "node_id": node.node_id,
                "job_id": job.job_id,
                "accepted": accepted,
                "score": score_node(node, job),
            },
        )
        self.events.append(event)
        log.info("assign_job node=%s job=%s accepted=%s", node.node_id, job.job_id, accepted)
        return accepted

    def _choose_model(self, prompt: str, requested_model: str | None = None) -> str:
        if requested_model and requested_model.strip():
            return requested_model.strip()

        p = (prompt or "").lower()

        if any(x in p for x in [
            "refactor", "python", "powershell", "bug", "traceback", "exception",
            "write code", "edit file", "patch", "function", "class", "fastapi"
        ]):
            return "qwen2.5-coder:14b"

        if len(prompt) > 2500 or any(x in p for x in [
            "analyze", "compare", "design", "architecture", "plan", "reason",
            "investigate", "root cause", "debug this", "step by step"
        ]):
            return "qwen2.5:14b-instruct-32k"

        return "qwen2.5:7b-instruct"

    def _generate_via_swarm(self, model: str, prompt: str, timeout: float | None = None) -> str:
        url = f"{self.swarm_base_url}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": False,
        }

        with httpx.Client(timeout=timeout if timeout is not None else self.swarm_timeout) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            raw = r.text
            if not raw or not raw.strip():
                raise RuntimeError("Empty JSON response from swarm endpoint")

            if raw.lstrip().startswith("data:"):
                chunks = []
                for line in raw.splitlines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload_line = line[5:].strip()
                    if payload_line == "[DONE]":
                        break
                    try:
                        event = json.loads(payload_line)
                    except json.JSONDecodeError:
                        continue
                    choices = event.get("choices", [])
                    if not choices:
                        continue
                    choice = choices[0]
                    if isinstance(choice, dict):
                        delta = choice.get("delta", {})
                        if isinstance(delta, dict):
                            content = delta.get("content", "")
                            if content:
                                chunks.append(content)
                        message = choice.get("message", {})
                        if isinstance(message, dict):
                            content = message.get("content", "")
                            if content:
                                chunks.append(content)

                if chunks:
                    return "".join(chunks)

                raise RuntimeError("SSE stream from swarm contained no content")

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                preview = raw[:300].replace("\n", "\\n")
                raise RuntimeError(f"Invalid JSON from swarm endpoint: {preview}") from e

        choices = data.get("choices", [])
        if not choices:
            return ""

        message = choices[0].get("message", {})
        if isinstance(message, dict):
            return message.get("content", "") or ""
        return ""

    def generate(self, model: str | None, prompt: str) -> tuple[str, str]:
        chosen_model = self._choose_model(prompt=prompt, requested_model=model)
        used_path = "swarm"

        try:
            result = self._generate_via_swarm(model=chosen_model, prompt=prompt)
            if not result:
                raise RuntimeError("Empty response from swarm")
        except Exception as e:
            log.warning("swarm path failed, falling back to ollama: %s", e)
            used_path = "ollama"
            result = self.ollama.generate(model=chosen_model, prompt=prompt)

        event = EventEnvelope.create(
            event_type="llm.generate",
            source="orchestrator",
            payload={
                "requested_model": model,
                "chosen_model": chosen_model,
                "prompt_len": len(prompt),
                "result_len": len(result),
                "path": used_path,
            },
        )
        self.events.append(event)
        return result, chosen_model

    async def evolve(self) -> None:
        log.info("Orchestrator.evolve(): Starting evolution cycle")
        kernel, metrics = await self.simulation.run(steps=1)
        event = EventEnvelope.create(
            event_type="swarm.evolve",
            source="orchestrator",
            payload={
                "generation": getattr(kernel, "generation", None),
                "best_fitness": getattr(metrics, "best_fitness", None),
            },
        )
        self.events.append(event)
        log.info(
            "Orchestrator.evolve(): Evolution cycle complete generation=%s best_fitness=%s",
            getattr(kernel, "generation", None),
            getattr(metrics, "best_fitness", None),
        )

    async def run_agent_step(self) -> dict:
        log.info("Orchestrator.run_agent_step(): Starting agent step")
        return {
            "status": "success",
            "message": "Agent step completed (placeholder)",
        }

