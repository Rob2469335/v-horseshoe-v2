from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import QdrantClient

from .event_bus import event_bus
from .memory_synthesizer import MemorySynthesizer


@dataclass
class EvaluationResult:
    trace_id: str
    score: float
    verdict: str
    reasons: list[str]


class RuntimeWorker:
    def __init__(self) -> None:
        self.memory_synthesizer = MemorySynthesizer(
            QdrantClient(url="http://127.0.0.1:6333"),
            collection="runtime_memory_episodes",
        )

    def evaluate_trace(self, trace_id: str) -> EvaluationResult:
        events = event_bus.list_events(trace_id)

        failed = any(e.status.value == "failed" for e in events)

        result = EvaluationResult(
            trace_id=trace_id,
            score=0.4 if failed else 1.0,
            verdict="needs_healing" if failed else "healthy",
            reasons=["failure detected"] if failed else ["ok"],
        )

        self.memory_synthesizer.synthesize_trace(trace_id)

        return result
