from __future__ import annotations

import time
from abc import ABC, abstractmethod

from .models import RouteDecision

ROLE_CAPABILITIES = {
    "vision": {"vision"},
    "embedding": {"embedding"},
    "reranker": {"rerank"},
    "retrieval": {"embedding", "rerank"},
    "coder": {"code"},
    "deep_coder": {"code", "long_context"},
    "planner": {"reasoning", "long_context"},
    "reasoning": {"reasoning", "long_context"},
    "writer": {"writing", "long_context"},
    "fast": {"fast"},
}

ROLE_PRIORITY = {
    "vision": 120.0,
    "embedding": 120.0,
    "reranker": 115.0,
    "retrieval": 110.0,
    "coder": 100.0,
    "deep_coder": 105.0,
    "planner": 98.0,
    "reasoning": 95.0,
    "writer": 88.0,
    "fast": 70.0,
}

def _capset(value):
    return {str(x).lower() for x in (value or [])}

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

    def _score_candidate(self, *, router, name: str, desired_role: str, now: float) -> tuple[float, str, dict]:
        profile = router.profiles.get(name)
        state = router.get_state(name)
        meta: dict = {}

        if state.cooldown_until > now:
            state.last_penalty = 1e9
            state.last_score = -1e9
            return -1e9, "cooldown", {"cooldown": True}

        score = 0.0
        reasons: list[str] = []

        if profile:
            role = profile.role
            caps = _capset(profile.capabilities)
            required = ROLE_CAPABILITIES.get(desired_role, set())

            if role == desired_role:
                score += ROLE_PRIORITY.get(desired_role, 75.0)
                reasons.append("role_match")
            elif desired_role == router.default_role and role == router.default_role:
                score += 25.0
                reasons.append("default_role_bias")
            elif required and required & caps:
                score += 35.0 + 8.0 * len(required & caps)
                reasons.append("capability_match")
            else:
                reasons.append("role_mismatch")

            if desired_role in {"vision", "embedding", "reranker", "retrieval"} and not (ROLE_CAPABILITIES.get(desired_role, set()) & caps):
                score -= 80.0
                reasons.append("missing_required_capability")

            if "long_context" in caps:
                score += 8.0
            if "fast" in caps:
                score += 5.0 if desired_role == router.default_role or desired_role == "fast" else 0.0
            if "code" in caps and desired_role in {"coder", "deep_coder", "planner"}:
                score += 12.0
            if "reasoning" in caps and desired_role in {"reasoning", "planner", "deep_coder"}:
                score += 10.0
            if "writing" in caps and desired_role == "writer":
                score += 10.0

            priority = float(profile.metadata.get("priority", 0.0)) if isinstance(profile.metadata, dict) else 0.0
            score += priority
            meta.update({"capabilities": sorted(caps), "priority": priority})

        failure_penalty = min(60.0, float(state.failures * 6))
        score -= failure_penalty
        reasons.append("failure_penalty" if failure_penalty else "no_failure_penalty")

        if state.total_requests > 0 and state.total_latency_ms > 0:
            avg_latency = state.total_latency_ms / state.total_requests
            latency_penalty = min(30.0, avg_latency / 175.0)
            score -= latency_penalty
            reasons.append("latency_penalty")
        else:
            latency_penalty = 0.0

        recency_bonus = 0.0
        if state.last_success_at > 0:
            recency_bonus = min(8.0, max(0.0, (now - state.last_success_at) / 3600.0))
            score += recency_bonus
            reasons.append("recent_success")

        state.last_penalty = failure_penalty + latency_penalty
        state.last_score = score
        meta.update({
            "score": score,
            "failure_penalty": failure_penalty,
            "latency_penalty": latency_penalty,
            "recency_bonus": recency_bonus,
        })
        return score, ",".join(reasons), meta

    def select_model(
        self,
        *,
        router: object,
        candidates: list[str],
        role: str | None = None,
        allow_fallback: bool = True,
    ) -> RouteDecision:
        strategy_name = self.name
        desired_role = role or router.default_role

        if not candidates:
            return RouteDecision(
                model="",
                role=desired_role,
                reason="no_candidates",
                fallback=True,
                strategy=strategy_name,
                metadata={"strategy": strategy_name, "desired_role": desired_role},
            )

        now = time.time()
        ranked: list[tuple[str, float, str, dict]] = []

        for name in candidates:
            score, reason, meta = self._score_candidate(router=router, name=name, desired_role=desired_role, now=now)
            ranked.append((name, score, reason, meta))

        ranked.sort(key=lambda item: item[1], reverse=True)
        best_name, best_score, best_reason, best_meta = ranked[0]
        best_state = router.get_state(best_name)

        if best_score > 0:
            return RouteDecision(
                model=best_name,
                role=best_state.role,
                reason=best_reason,
                fallback=False,
                strategy=strategy_name,
                metadata={"strategy": strategy_name, "desired_role": desired_role, **best_meta},
            )

        if allow_fallback:
            return RouteDecision(
                model=best_name,
                role=best_state.role,
                reason=f"fallback:{best_reason}",
                fallback=True,
                strategy=strategy_name,
                metadata={"strategy": strategy_name, "desired_role": desired_role, **best_meta},
            )

        return RouteDecision(
            model="",
            role=desired_role,
            reason="no_viable_route",
            fallback=True,
            strategy=strategy_name,
            metadata={"strategy": strategy_name, "desired_role": desired_role},
        )

class DeepStrategy(DefaultStrategy):
    @property
    def name(self) -> str:
        return "deep"

    def _score_candidate(self, *, router, name: str, desired_role: str, now: float) -> tuple[float, str, dict]:
        score, reason, meta = super()._score_candidate(router=router, name=name, desired_role=desired_role, now=now)
        profile = router.profiles.get(name)
        if profile and score > -1e8:
            caps = _capset(profile.capabilities)
            long_bias = 0.0
            if profile.max_tokens >= 16000:
                long_bias += 15.0
            if profile.max_tokens >= 32000:
                long_bias += 10.0
            if "long_context" in caps:
                long_bias += 10.0
            if "reasoning" in caps:
                long_bias += 10.0
            if "code" in caps:
                long_bias += 6.0
            if profile.role in {"planner", "reasoning", "deep_coder", "writer"}:
                long_bias += 8.0
            score += long_bias
            reason = reason + (",deep_bias" if long_bias else "")
            meta["deep_bias"] = long_bias
            meta["score"] = score
        return score, reason, meta
