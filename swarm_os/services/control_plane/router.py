from __future__ import annotations

import time
from typing import Iterable

from .models import ModelProfile, ModelState, PlanStep, RouteDecision, StepDecision

class Router:
    def __init__(
        self,
        *,
        profiles: Iterable[ModelProfile] | None = None,
        default_role: str = "fast",
        cooldown_multiplier: float = 2.0,
    ) -> None:
        self.profiles = {p.name: p for p in profiles} if profiles else {}
        self.default_role = default_role
        self.cooldown_multiplier = cooldown_multiplier
        self.states: dict[str, ModelState] = {}

        for profile in self.profiles.values():
            self.states[profile.name] = ModelState(
                name=profile.name,
                role=profile.role,
            )

    def register_model(self, profile: ModelProfile) -> None:
        self.profiles[profile.name] = profile
        if profile.name not in self.states:
            self.states[profile.name] = ModelState(
                name=profile.name,
                role=profile.role,
            )

    def get_state(self, model: str) -> ModelState:
        if model not in self.states:
            profile = self.profiles.get(model)
            role = profile.role if profile else self.default_role
            self.states[model] = ModelState(name=model, role=role)
        return self.states[model]

    def is_in_cooldown(self, model: str) -> bool:
        return time.time() < self.get_state(model).cooldown_until

    def route_model(
        self,
        *,
        candidates: list[str],
        role: str | None = None,
        allow_fallback: bool = True,
    ) -> RouteDecision:
        if not candidates:
            return RouteDecision(
                model="",
                role=self.default_role,
                reason="no_candidates",
                fallback=True,
            )

        now = time.time()
        desired_role = role or self.default_role
        ranked: list[tuple[str, float, str]] = []

        for name in candidates:
            profile = self.profiles.get(name)
            state = self.get_state(name)

            if state.cooldown_until > now:
                state.last_penalty = 1e9
                state.last_score = -1e9
                ranked.append((name, -1e9, "cooldown"))
                continue

            score = 0.0
            reason_parts: list[str] = []

            if profile and profile.role == desired_role:
                score += 100.0
                reason_parts.append("role_match")
            elif profile and desired_role == self.default_role:
                score += 25.0
                reason_parts.append("default_role_bias")
            else:
                reason_parts.append("role_mismatch")

            failure_penalty = min(50.0, float(state.failures * 5))
            score -= failure_penalty

            if state.total_requests > 0 and state.total_latency_ms > 0:
                avg_latency = state.total_latency_ms / state.total_requests
                latency_penalty = min(25.0, avg_latency / 200.0)
                score -= latency_penalty
                reason_parts.append("latency_penalty")
            else:
                latency_penalty = 0.0

            state.last_penalty = failure_penalty + latency_penalty
            state.last_score = score
            ranked.append((name, score, ",".join(reason_parts)))

        ranked.sort(key=lambda item: item[1], reverse=True)
        best_name, best_score, best_reason = ranked[0]
        best_state = self.get_state(best_name)

        if best_score > 0:
            return RouteDecision(
                model=best_name,
                role=best_state.role,
                reason=best_reason,
                fallback=False,
                metadata={"score": best_score},
            )

        if allow_fallback:
            return RouteDecision(
                model=best_name,
                role=best_state.role,
                reason=f"fallback:{best_reason}",
                fallback=True,
                metadata={"score": best_score},
            )

        return RouteDecision(
            model="",
            role=desired_role,
            reason="no_viable_route",
            fallback=True,
        )

    def record_success(self, model: str, latency_ms: float) -> None:
        state = self.get_state(model)
        state.successes += 1
        state.total_requests += 1
        state.total_latency_ms += max(0.0, latency_ms)
        state.last_attempt_at = time.time()
        state.last_success_at = state.last_attempt_at
        state.cooldown_until = 0.0
        state.failures = max(0, state.failures - 1)

    def record_failure(
        self,
        model: str,
        cooldown_seconds: float | None = None,
    ) -> None:
        state = self.get_state(model)
        state.failures += 1
        state.total_requests += 1
        state.last_attempt_at = time.time()

        profile = self.profiles.get(model)
        base_cooldown = profile.cooldown_seconds if profile else 5.0
        applied_cooldown = cooldown_seconds if cooldown_seconds is not None else base_cooldown * self.cooldown_multiplier
        state.cooldown_until = state.last_attempt_at + max(0.0, applied_cooldown)

    def route(self, step: PlanStep) -> StepDecision:
        if step.kind in {"tool", "retrieve"}:
            return StepDecision(
                action="delegate",
                reason=f"route:{step.kind}",
                target="tool-runtime",
            )

        if step.kind in {"analyze", "synthesize"}:
            target = step.assigned_to if step.assigned_to and step.assigned_to != "none" else self.default_role
            return StepDecision(
                action="delegate",
                reason=f"route:{step.kind}",
                target=target,
            )

        return StepDecision(
            action="complete",
            reason=f"route:{step.kind}",
            target=step.assigned_to,
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
