from __future__ import annotations

import threading
import time
from typing import Any, Iterable

from .models import ModelProfile, ModelState, PlanStep, RouteDecision, StepDecision
from .strategy_registry import strategy_registry


class Router:
    def __init__(
        self,
        *,
        profiles: Iterable[ModelProfile] | None = None,
        default_role: str = "reasoning",
        cooldown_multiplier: float = 2.0,
    ) -> None:
        self.profiles = {p.name: p for p in profiles} if profiles else {}
        self.default_role = default_role
        self.cooldown_multiplier = cooldown_multiplier
        # Guards the shared ModelState scratch fields that the strategy mutates
        # from the to_thread worker (last_penalty/last_score pair-write) against
        # concurrent route_model calls. record_success/record_failure run on the
        # event loop and touch disjoint counters; the pair-write in
        # _score_candidate is the torn-write hazard.
        self._state_lock = threading.Lock()
        self.states: dict[str, ModelState] = {}
        for profile in self.profiles.values():
            self.states[profile.name] = ModelState(name=profile.name, role=profile.role)

    def register_model(self, profile: ModelProfile) -> None:
        self.profiles[profile.name] = profile
        if profile.name not in self.states:
            self.states[profile.name] = ModelState(name=profile.name, role=profile.role)

    def get_state(self, model: str) -> ModelState:
        if model not in self.states:
            profile = self.profiles.get(model)
            role = profile.role if profile else self.default_role
            if profile:
                self.states[model] = ModelState(name=model, role=role)
            else:
                return ModelState(name=model, role=role)
        return self.states[model]

    def is_in_cooldown(self, model: str) -> bool:
        return time.time() < self.get_state(model).cooldown_until

    def export_states(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "name": state.name,
                "role": state.role,
                "failures": state.failures,
                "successes": state.successes,
                "total_requests": state.total_requests,
                "total_latency_ms": state.total_latency_ms,
                "cooldown_until": state.cooldown_until,
                "last_success_at": state.last_success_at,
                "last_attempt_at": state.last_attempt_at,
                "last_score": state.last_score,
                "last_penalty": state.last_penalty,
                "metadata": state.metadata,
            }
            for name, state in self.states.items()
        }

    def import_states(self, data: dict[str, dict[str, Any]]) -> None:

        for name, values in data.items():
            state = self.get_state(name)
            state.failures = values.get("failures", 0)
            state.successes = values.get("successes", 0)
            state.total_requests = values.get("total_requests", 0)
            state.total_latency_ms = values.get("total_latency_ms", 0.0)
            state.cooldown_until = values.get("cooldown_until", 0.0)
            state.last_success_at = values.get("last_success_at", 0.0)
            state.last_attempt_at = values.get("last_attempt_at", 0.0)
            state.last_score = values.get("last_score", 0.0)
            state.last_penalty = values.get("last_penalty", 0.0)
            state.metadata = values.get("metadata", {})

    def candidates_for_role(self, role: str | None) -> list[str]:
        role = (role or self.default_role).lower()
        primary = [
            name for name, profile in self.profiles.items() if profile.role == role
        ]
        if primary:
            return primary

        capability_fallback: list[str] = []
        for name, profile in self.profiles.items():
            caps = {str(c).lower() for c in profile.capabilities}
            if role == "vision" and "vision" in caps:
                capability_fallback.append(name)
            elif role == "embedding" and "embedding" in caps:
                capability_fallback.append(name)
            elif role == "reranker" and "rerank" in caps:
                capability_fallback.append(name)
            elif role == "retrieval" and ("embedding" in caps or "rerank" in caps):
                capability_fallback.append(name)
            elif role in {"coding", "coder", "deep_coder"} and "code" in caps:
                capability_fallback.append(name)
            elif role in {"planner", "reasoning"} and (
                "reasoning" in caps or "long_context" in caps
            ):
                capability_fallback.append(name)
            elif role == "writer" and ("writing" in caps or "long_context" in caps):
                capability_fallback.append(name)

        if capability_fallback:
            return capability_fallback

        if role == self.default_role:
            return list(self.profiles.keys())

        return list(self.profiles.keys())

    async def route_model(
        self,
        *,
        candidates: list[str] | None = None,
        role: str | None = None,
        allow_fallback: bool = True,
    ) -> RouteDecision:
        role = (role or self.default_role).lower()
        pool = candidates if candidates is not None else self.candidates_for_role(role)
        if not pool:
            pool = list(self.profiles.keys())

        import asyncio

        strategy = await asyncio.to_thread(
            strategy_registry.get_active,
            context={
                "role": role,
                "candidates": list(pool),
                "allow_fallback": allow_fallback,
            },
        )
        decision = await asyncio.to_thread(
            strategy.select_model,
            router=self,
            candidates=list(pool),
            role=role,
            allow_fallback=allow_fallback,
        )

        if decision.model:
            decision.metadata.setdefault("requested_role", role)
            decision.metadata.setdefault("candidate_count", len(pool))
            decision.metadata.setdefault("strategy", decision.strategy)
        return decision

    def record_success(self, model: str, latency_ms: float) -> None:
        state = self.get_state(model)
        state.successes += 1
        state.total_requests += 1
        state.total_latency_ms += max(0.0, latency_ms)
        state.last_attempt_at = time.time()
        state.last_success_at = state.last_attempt_at
        state.cooldown_until = 0.0
        state.failures = int(
            state.failures * 0.5
        )  # Exponential decay instead of linear -1

    def record_failure(self, model: str, cooldown_seconds: float | None = None) -> None:
        state = self.get_state(model)
        state.failures += 1
        state.total_requests += 1
        state.last_attempt_at = time.time()
        profile = self.profiles.get(model)
        base_cooldown = profile.cooldown_seconds if profile else 5.0
        applied_cooldown = (
            cooldown_seconds
            if cooldown_seconds is not None
            else base_cooldown * self.cooldown_multiplier
        )
        state.cooldown_until = state.last_attempt_at + max(0.0, applied_cooldown)

    def route(self, step: PlanStep) -> StepDecision:
        kind = step.kind.lower()
        goal = (step.goal or "").lower()
        raw_assigned = step.assigned_to if step.assigned_to else "none"
        assigned = raw_assigned if raw_assigned != "none" else self.default_role

        if kind in {"tool", "retrieve"}:
            return StepDecision(
                action="delegate",
                reason=f"route:{kind}",
                target="tool-runtime",
                metadata={"kind": kind},
            )

        if kind == "vision":
            return StepDecision(
                action="delegate",
                reason="route:vision",
                target="vision",
                metadata={"kind": kind},
            )

        if kind in {"embed", "embedding", "rerank"}:
            target = "reranker" if kind == "rerank" else "embedding"
            return StepDecision(
                action="delegate",
                reason=f"route:{kind}",
                target=target,
                metadata={"kind": kind},
            )

        if kind in {"analyze", "synthesize", "plan", "planner", "reason"}:
            target = assigned
            if raw_assigned == "none":
                if any(
                    k in goal for k in ("code", "refactor", "bug", "patch", "implement")
                ):
                    target = "coding"
                elif any(
                    k in goal for k in ("plan", "strategy", "architect", "design")
                ):
                    target = "planner"
                elif any(k in goal for k in ("write", "draft", "summar", "explain")):
                    target = "writer"
                else:
                    target = self.default_role
            return StepDecision(
                action="delegate",
                reason=f"route:{kind}",
                target=target,
                metadata={"kind": kind, "goal": goal},
            )

        return StepDecision(
            action="complete",
            reason=f"route:{kind}",
            target=assigned,
            metadata={"kind": kind},
        )


def attach_to_registry(router: Router, registry: object) -> None:
    if hasattr(registry, "register"):
        registry.register("router", router)


def evolve_plugin_weights(registry: object) -> dict[str, float]:
    if not hasattr(registry, "ranked"):
        return {}
    ranked = registry.ranked()
    return {
        getattr(item, "name", ""): float(getattr(item, "fitness", 0.0))
        for item in ranked
        if getattr(item, "name", "")
    }
