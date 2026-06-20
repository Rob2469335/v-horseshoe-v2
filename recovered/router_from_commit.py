from __future__ import annotations

import hashlib
import time
import math
import random
from dataclasses import dataclass
from typing import Dict, Iterable, List

from .models import (
    ModelProfile,
    ModelState,
    PlanStep,
    RouteDecision,
    RouteFeedback,
    RouteOutcome,
    RoutingPolicy,
    StepDecision,
)


@dataclass(slots=True)
class RoutePolicy:
    mode: str = "balanced"
    max_latency_ms: float | None = None
    max_model_params_b: int | None = None
    allowed_models: List[str] | None = None
    quality_weight: float = 1.0
    latency_weight: float = 1.0
    cost_weight: float = 1.0

MODEL_COSTS: Dict[str, float] = {
    "qwen2.5-coder:3b": 1.0,
    "qwen2.5-coder:7b": 2.0,
    "qwen2.5-coder:14b": 4.0,
    "qwen2.5-coder:14b-32k": 6.0,
    "qwen2.5:3b-instruct": 1.0,
    "qwen2.5:7b-instruct": 2.0,
    "mistral-nemo:12b": 3.0,
    "qwen2.5:14b-instruct": 4.0,
    "qwen2.5:14b-instruct-32k": 5.0,
    "qwen3:14b": 5.0,
}


class Router:
    def __init__(
        self,
        *,
        profiles: Iterable[ModelProfile] | None = None,
        default_role: str = "fast",
        cooldown_multiplier: float = 2.0,
    ) -> None:
        self.profiles = {p.name: p for p in profiles} if profiles else {}
        self.policy = RoutingPolicy(
            default_role=default_role,
            cooldown_multiplier=cooldown_multiplier,
        )
        self.states: Dict[str, ModelState] = {}
        self.selection_counters: Dict[str, int] = {}

        for profile in self.profiles.values():
            self.register_model(profile)

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
            role = profile.role if profile else self.policy.default_role
            self.states[model] = ModelState(name=model, role=role)
        return self.states[model]

    def is_in_cooldown(self, model: str) -> bool:
        return time.time() < self.get_state(model).cooldown_until

    def filter_candidates(self, candidates: List[str]) -> List[str]:
        return [name for name in candidates if isinstance(name, str) and name.strip()]

    def _extract_model_size_b(self, name: str) -> int | None:
        text = (name or "").lower()
        for size in (32, 27, 24, 14, 12, 8, 7, 3):
            if f"{size}b" in text:
                return size
        return None

    def select_policy(self, routing_mode: str) -> RoutePolicy:
        mode = (routing_mode or "balanced").strip().lower()
        if mode == "speed":
            return RoutePolicy(
                mode="speed",
                max_latency_ms=8000.0,
                max_model_params_b=7,
                quality_weight=0.8,
                latency_weight=2.0,
                cost_weight=2.0,
            )
        if mode == "deep":
            return RoutePolicy(
                mode="deep",
                max_latency_ms=None,
                max_model_params_b=None,
                quality_weight=1.2,
                latency_weight=0.35,
                cost_weight=0.5,
            )
        return RoutePolicy(
            mode="balanced",
            max_latency_ms=None,
            max_model_params_b=None,
            quality_weight=1.0,
            latency_weight=1.0,
            cost_weight=1.0,
        )

    def apply_policy_filter(self, candidates: List[str], policy: RoutePolicy) -> List[str]:
        filtered = self.filter_candidates(candidates)

        if policy.allowed_models:
            allowed = {m.lower() for m in policy.allowed_models}
            filtered = [name for name in filtered if name.lower() in allowed]

        if policy.max_model_params_b is not None:
            sized = []
            unknown = []
            for name in filtered:
                size_b = self._extract_model_size_b(name)
                if size_b is None:
                    unknown.append(name)
                elif size_b <= policy.max_model_params_b:
                    sized.append(name)
            filtered = sized or filtered

        if policy.max_latency_ms is not None:
            latency_ok = []
            for name in filtered:
                state = self.get_state(name)
                if state.total_requests <= 0:
                    latency_ok.append(name)
                    continue
                avg_latency = state.total_latency_ms / state.total_requests
                if avg_latency <= policy.max_latency_ms:
                    latency_ok.append(name)
            filtered = latency_ok or filtered

        return filtered

    def get_model_cost(self, name: str) -> float:
        return MODEL_COSTS.get(name, 3.0)

    def estimate_required_tokens(self, prompt: str | None) -> int:
        text = (prompt or "").strip()
        if not text:
            return 2048
        estimated = max(2048, min(8192, len(text) * 4))
        return estimated

    def get_overcapacity_penalty(self, name: str, prompt: str | None, routing_mode: str) -> float:
        profile = self.profiles.get(name)
        if not profile:
            return 0.0

        required_tokens = self.estimate_required_tokens(prompt)
        if required_tokens <= 0:
            return 0.0

        ratio = profile.max_tokens / required_tokens
        if ratio <= 2.0:
            return 0.0

        excess = ratio - 2.0
        if routing_mode == "speed":
            return min(20.0, excess * 2.5)
        if routing_mode == "balanced":
            return min(10.0, excess * 1.0)
        return min(4.0, excess * 0.35)

    def score_model(self, name: str, *, role: str, routing_mode: str = "balanced", prompt: str | None = None, now: float | None = None) -> tuple[float, str]:
        now = now if now is not None else time.time()
        profile = self.profiles.get(name)
        state = self.get_state(name)

        if state.cooldown_until > now:
            return -1e9, "cooldown"

        route_policy = self.select_policy(routing_mode)
        quality_weight = route_policy.quality_weight
        latency_weight = route_policy.latency_weight
        cost_weight = route_policy.cost_weight

        score = 0.0

        if profile:
            if profile.role == role:
                score += 100.0 * quality_weight
            elif profile.role == "fast" and role == "fast":
                score += 50.0 * quality_weight

            if profile.max_tokens >= 32768:
                score += 10.0 * quality_weight

            name_l = name.lower()
            if "14b" in name_l:
                score += 8.0 * quality_weight
            elif "12b" in name_l:
                score += 6.0 * quality_weight
            elif "7b" in name_l:
                score += 4.0 * quality_weight
            elif "3b" in name_l:
                score += 1.0 * quality_weight

        if state.successes > 0:
            score += min(20.0, state.successes * 1.5) * quality_weight

        if state.failures > 0:
            score -= min(50.0, state.failures * 5.0)

        if state.critic_samples > 0:
            avg_critic = state.total_critic_score / state.critic_samples
            score += (avg_critic * 20.0) * quality_weight

        if state.rejections > 0:
            score -= min(25.0, state.rejections * 4.0)

        if state.total_requests > 0:
            avg_latency = state.total_latency_ms / state.total_requests
            if avg_latency > 0:
                score -= min(15.0, avg_latency / 1000.0) * latency_weight

        score -= self.get_model_cost(name) * cost_weight
        score -= self.get_overcapacity_penalty(name, prompt, routing_mode)

        return score, "scored"

    def choose_with_competition_band(
        self,
        *,
        scored: List[tuple[str, float, str]],
        prompt: str | None,
        competition_band: float,
        routing_mode: str = "balanced",
        temperature: float = 0.7,
        sigma: float = 0.0,
        top_k: int = 3,
    ) -> tuple[str, float, str, Dict[str, float]]:
        import math
        import random

        if not scored:
            raise ValueError("scored must not be empty")

        temperature = max(1e-6, float(temperature))
        sigma = max(0.0, float(sigma))
        top_k = max(1, int(top_k))

        pool = list(scored[:top_k])
        adjusted: List[tuple[str, float, str]] = []

        for name, score, reason in pool:
            value = float(score)
            if sigma > 0.0:
                value += random.gauss(0.0, sigma)
            adjusted.append((name, value, reason))

        max_score = max(score for _, score, _ in adjusted)
        exps = [math.exp((score - max_score) / temperature) for _, score, _ in adjusted]
        total = sum(exps) or 1.0
        probabilities = [value / total for value in exps]

        draw = random.random()
        cumulative = 0.0

        for (name, score, reason), probability in zip(adjusted, probabilities):
            cumulative += probability
            if draw <= cumulative:
                return name, score, reason, {
                    candidate: prob
                    for (candidate, _, _), prob in zip(adjusted, probabilities)
                }

        name, score, reason = adjusted[-1]
        return name, score, reason, {
            candidate: prob
            for (candidate, _, _), prob in zip(adjusted, probabilities)
        }
    def route_model(
        self,
        *,
        candidates: List[str],
        role: str | None = None,
        allow_fallback: bool = True,
        max_cooldown_factor: float = 3.0,
    ) -> RouteDecision:
        outcome = self.route_model_detailed(
            candidates=candidates,
            role=role,
            allow_fallback=allow_fallback,
            max_cooldown_factor=max_cooldown_factor,
        )
        return RouteDecision(
            model=outcome.model,
            role=outcome.role,
            reason=outcome.reason,
            fallback=outcome.fallback,
            metadata=dict(outcome.metadata),
        )

    def route_model_detailed(
        self,
        *,
        candidates: List[str],
        role: str | None = None,
        allow_fallback: bool | None = None,
        max_cooldown_factor: float | None = None,
        routing_mode: str = "balanced",
        prompt: str | None = None,
        competition_band: float = 0.0,
        gap_threshold: float = 0.08,
        stochastic_top_k: int = 3,
        stochastic_temperature: float = 0.7,
        stochastic_sigma: float = 0.0,
    ) -> RouteOutcome:
        now = time.time()
        role = role or self.policy.default_role
        allow_fallback = self.policy.allow_fallback if allow_fallback is None else allow_fallback
        max_cooldown_factor = self.policy.max_cooldown_factor if max_cooldown_factor is None else max_cooldown_factor
        route_policy = self.select_policy(routing_mode)

        filtered = self.apply_policy_filter(candidates, route_policy)
        if not filtered:
            return RouteOutcome(
                model="",
                role=role,
                reason="no_candidates",
                fallback=True,
                score=0.0,
                candidates=[],
                metadata={
                    "role": role,
                    "routing_mode": routing_mode,
                    "policy_mode": route_policy.mode,
                },
            )

        scored: List[tuple[str, float, str]] = []
        for name in filtered:
            score, reason = self.score_model(
                name,
                role=role,
                routing_mode=routing_mode,
                prompt=prompt,
                now=now,
            )
            scored.append((name, score, reason))

        scored.sort(key=lambda x: x[1], reverse=True)

        viable = [(n, s, r) for n, s, r in scored if s >= self.policy.minimum_score]
        if viable:
            top_name, top_score, top_reason = viable[0]
            second_score = viable[1][1] if len(viable) > 1 else 0.0
            top_gap = float(top_score) - float(second_score)

            metadata = {
                "role": role,
                "routing_mode": routing_mode,
                "policy_mode": route_policy.mode,
                "competition_band": competition_band,
                "gap_threshold": gap_threshold,
                "top_gap": top_gap,
                "selection_mode": "deterministic" if top_gap > gap_threshold else "stochastic",
                "scored_candidates": [
                    {"model": n, "score": s, "reason": r} for n, s, r in scored
                ],
                "viable_candidates": [
                    {"model": n, "score": s, "reason": r} for n, s, r in viable
                ],
                "stochastic_top_k": stochastic_top_k,
                "stochastic_temperature": stochastic_temperature,
                "stochastic_sigma": stochastic_sigma,
            }

            if top_gap > gap_threshold:
                chosen_role = self.get_state(top_name).role
                return RouteOutcome(
                    model=top_name,
                    role=chosen_role,
                    reason=f"{top_reason}:{chosen_role}",
                    fallback=False,
                    score=top_score,
                    candidates=filtered,
                    metadata=metadata,
                )

            name, score, reason, probabilities = self.choose_with_competition_band(
                scored=viable,
                prompt=prompt,
                competition_band=competition_band,
                routing_mode=routing_mode,
                temperature=stochastic_temperature,
                sigma=stochastic_sigma,
                top_k=stochastic_top_k,
            )
            chosen_role = self.get_state(name).role
            metadata["probabilities"] = probabilities

            return RouteOutcome(
                model=name,
                role=chosen_role,
                reason=f"{reason}:{chosen_role}",
                fallback=False,
                score=score,
                candidates=filtered,
                metadata=metadata,
            )

        if allow_fallback and scored:
            best, best_score, best_reason = scored[0]
            chosen_role = self.get_state(best).role
            return RouteOutcome(
                model=best,
                role=chosen_role,
                reason=f"fallback:{best_reason}",
                fallback=True,
                score=best_score,
                candidates=filtered,
                metadata={
                    "role": role,
                    "routing_mode": routing_mode,
                    "policy_mode": route_policy.mode,
                    "competition_band": competition_band,
                    "gap_threshold": gap_threshold,
                    "selection_mode": "fallback",
                    "scored_candidates": [
                        {"model": n, "score": s, "reason": r} for n, s, r in scored
                    ],
                    "max_cooldown_factor": max_cooldown_factor,
                },
            )

        return RouteOutcome(
            model=filtered[0],
            role=self.get_state(filtered[0]).role,
            reason="no_viable_candidate",
            fallback=True,
            score=0.0,
            candidates=filtered,
            metadata={
                "role": role,
                "routing_mode": routing_mode,
                "policy_mode": route_policy.mode,
                "selection_mode": "no_viable_candidate",
            },
        )
    def record_success(self, model: str, latency_ms: float) -> None:
        state = self.get_state(model)
        state.successes += 1
        state.failures = max(0, state.failures - 1)
        state.total_latency_ms += latency_ms
        state.total_requests += 1
        state.last_success_at = time.time()
        state.last_attempt_at = time.time()
        state.cooldown_until = 0.0

    def record_failure(self, model: str, cooldown_seconds: float | None = None) -> None:
        state = self.get_state(model)
        state.failures += 1
        state.total_requests += 1
        state.last_attempt_at = time.time()

        if cooldown_seconds is not None:
            state.cooldown_until = time.time() + cooldown_seconds
        else:
            profile = self.profiles.get(model)
            base_cooldown = profile.cooldown_seconds if profile else 5.0
            state.cooldown_until = time.time() + (base_cooldown * self.policy.cooldown_multiplier)

    def apply_feedback(self, feedback: RouteFeedback) -> ModelState:
        state = self.get_state(feedback.model)
        state.last_attempt_at = time.time()

        if feedback.latency_ms > 0:
            state.total_latency_ms += feedback.latency_ms

        if feedback.critic_score > 0:
            state.total_critic_score += feedback.critic_score
            state.critic_samples += 1
            state.last_critic_score = feedback.critic_score

        if feedback.accepted:
            state.successes += 1
            state.last_success_at = time.time()
            state.failures = max(0, state.failures - 1)
            state.cooldown_until = 0.0
        else:
            state.failures += 1
            state.rejections += 1
            profile = self.profiles.get(feedback.model)
            base_cooldown = profile.cooldown_seconds if profile else 5.0
            state.cooldown_until = time.time() + (base_cooldown * self.policy.cooldown_multiplier)

        state.total_requests += 1
        return state

    def route(self, step: PlanStep) -> StepDecision:
        if step.kind in {"tool", "retrieve"}:
            return StepDecision(action="delegate", reason=f"route:{step.kind}", target="tool-runtime")
        if step.kind in {"analyze", "synthesize"}:
            return StepDecision(action="delegate", reason=f"route:{step.kind}", target=step.assigned_to)
        return StepDecision(action="complete", reason=f"route:{step.kind}", target=step.assigned_to)


















