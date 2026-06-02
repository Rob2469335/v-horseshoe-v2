from __future__ import annotations

import time
from abc import ABC, abstractmethod

from .models import RouteDecision


class RoutingStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def select_model(
        self,
        *,
        router: object,
        candidates: list[str],
        role: str | None = None,
        allow_fallback: bool = True,
    ) -> RouteDecision:
        raise NotImplementedError


class DefaultStrategy(RoutingStrategy):
    @property
    def name(self) -> str:
        return "default"

    def select_model(
        self,
        *,
        router: object,
        candidates: list[str],
        role: str | None = None,
        allow_fallback: bool = True,
    ) -> RouteDecision:
        strategy_name = self.name

        if not candidates:
            return RouteDecision(
                model="",
                role=router.default_role,
                reason="no_candidates",
                fallback=True,
                strategy=strategy_name,
                metadata={"strategy": strategy_name},
            )

        now = time.time()
        desired_role = role or router.default_role
        ranked: list[tuple[str, float, str]] = []

        for name in candidates:
            profile = router.profiles.get(name)
            state = router.get_state(name)

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
            elif profile and desired_role == router.default_role:
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
        best_state = router.get_state(best_name)

        if best_score > 0:
            return RouteDecision(
                model=best_name,
                role=best_state.role,
                reason=best_reason,
                fallback=False,
                strategy=strategy_name,
                metadata={
                    "score": best_score,
                    "strategy": strategy_name,
                },
            )

        if allow_fallback:
            return RouteDecision(
                model=best_name,
                role=best_state.role,
                reason=f"fallback:{best_reason}",
                fallback=True,
                strategy=strategy_name,
                metadata={
                    "score": best_score,
                    "strategy": strategy_name,
                },
            )

        return RouteDecision(
            model="",
            role=desired_role,
            reason="no_viable_route",
            fallback=True,
            strategy=strategy_name,
            metadata={"strategy": strategy_name},
        )


class DeepStrategy(DefaultStrategy):
    @property
    def name(self) -> str:
        return "deep"
