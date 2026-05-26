# swarm_os/services/orchestrator.py
from __future__ import annotations

import logging

from ..core.settings import get_settings
from ..domain.models import SwarmJob, SwarmNode
from ..domain.policies import can_accept_job, score_node
from ..events.envelope import EventEnvelope
from ..events.store import EventStore
from ..infra.ollama import OllamaClient

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self) -> None:
        s           = get_settings()
        self.events = EventStore(s.events_dir)
        self.ollama = OllamaClient()

    def assign_job(self, node: SwarmNode, job: SwarmJob) -> bool:
        accepted = can_accept_job(node, job)
        event    = EventEnvelope.create(
            event_type = "job.assignment.checked",
            source     = "orchestrator",
            payload    = {
                "node_id":  node.node_id,
                "job_id":   job.job_id,
                "accepted": accepted,
                "score":    score_node(node, job),
            },
        )
        self.events.append(event)
        log.info("assign_job node=%s job=%s accepted=%s", node.node_id, job.job_id, accepted)
        return accepted

    def generate(self, model: str, prompt: str) -> str:
        result = self.ollama.generate(model=model, prompt=prompt)
        event  = EventEnvelope.create(
            event_type = "llm.generate",
            source     = "orchestrator",
            payload    = {
                "model":       model,
                "prompt_len":  len(prompt),
                "result_len":  len(result),
            },
        )
        self.events.append(event)
        return result

    def evolve(self) -> None:
        """Run one generation of swarm evolution."""
        log.info("Orchestrator.evolve(): Starting evolution cycle")
        
        # TODO: Integrate with genetics/selection modules
        log.info("Orchestrator.evolve(): Evolution cycle complete")

    async def run_agent_step(self) -> dict:
        """Run one step of agent execution using capability tools."""
        log.info("Orchestrator.run_agent_step(): Starting agent step")
        
        # TODO: Use AgentRuntime to execute capability tools
        return {
            "status": "success",
            "message": "Agent step completed (placeholder)",
        }
