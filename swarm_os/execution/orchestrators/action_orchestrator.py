"""
Module: action_orchestrator
Order: 18
Package: execution.orchestrators
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

from swarm_os.governance.constraints.decision_gate import DecisionGate, GateDecision


@dataclass(slots=True)
class OrchestrationResult:
    action: str
    decision: GateDecision
    status: str


class ActionOrchestrator:
    def __init__(self, gate: DecisionGate | None = None) -> None:
        self.gate = gate or DecisionGate()

    def run(self, action: str, context: dict) -> OrchestrationResult:
        decision = self.gate.evaluate(action=action, context=context)
        status = "blocked" if not decision.allowed else "ready"
        return OrchestrationResult(action=action, decision=decision, status=status)