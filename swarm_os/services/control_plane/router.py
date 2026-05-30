from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List

from .models import PlanStep, StepDecision, ModelProfile, ModelState, RouteDecision


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
        self.states: Dict[str, ModelState] = {}

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
        state = self.get_state(model)
        return time.time() < state.cooldown_until

    def route_model(
        self,
        *,
        candidates: List[str],
        role: str | None = None,
        allow_fallback: bool = True,
        max_cooldown_factor: float = 3.0,
    ) -> RouteDecision:
        now = time.time()
        role = role or self.default_role

        def score_model(name: str) -> tuple[float, str]:
            profile = self.profiles.get(name)
            state = self.get_state(name)

            if state.cooldown_until > now:
                return -1e9, "cooldown"

            base_score = 0.0
            if profile:
                if profile.role == role:
                    base_score += 100
                elif profile.role == "fast" and role == "fast":
                    base_score += 50

            if state.failures > 0:
                penalty = min(50, state.failures * 5)
                base_score -= penalty

            return base_score, "scored"

        scored = [(name, score_model(name)) for name in candidates]
        scored.sort(key=lambda x: x[1][0], reverse=True)

        for name, (score, reason) in scored:
            if score > 0:
                return RouteDecision(
                    model=name,
                    role=self.get_state(name).role,
                    reason=f"{reason}:{self.get_state(name).role}",
                    fallback=False,
                )

        if allow_fallback and scored:
            best = scored[0][0]
            return RouteDecision(
                model=best,
                role=self.get_state(best).role,
                reason="fallback:no_suitable_model",
                fallback=True,
            )

        return RouteDecision(
            model=candidates[0] if candidates else "",
            role=self.default_role,
            reason="no_candidates",
            fallback=True,
        )

    def record_success(
        self,
        model: str,
        latency_ms: float,
    ) -> None:
        state = self.get_state(model)
        state.failures = max(0, state.failures - 1)
        state.total_latency_ms += latency_ms
        state.total_requests += 1
        state.last_success_at = time.time()
        state.cooldown_until = 0.0

    def record_failure(
        self,
        model: str,
        cooldown_seconds: float | None = None,
    ) -> None:
        state = self.get_state(model)
        state.failures += 1
        state.last_attempt_at = time.time()
        if cooldown_seconds is not None:
            state.cooldown_until = time.time() + cooldown_seconds
        else:
            base = self.profiles.get(model, None)
            base_cooldown = base.cooldown_seconds if base else 5.0
            state.cooldown_until = time.time() + base_cooldown * self.cooldown_multiplier

    def route(self, step: PlanStep) -> StepDecision:
        if step.kind in {"tool", "retrieve"}:
            return StepDecision(action="delegate", reason=f"route:{step.kind}", target="tool-runtime")
        if step.kind in {"analyze", "synthesize"}:
            return StepDecision(action="delegate", reason=f"route:{step.kind}", target=step.assigned_to)
        return StepDecision(action="complete", reason=f"route:{step.kind}", target=step.assigned_to)
