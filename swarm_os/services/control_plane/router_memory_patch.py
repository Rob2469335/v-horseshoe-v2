"""
router_memory_patch.py
Proactive routing extension for Router.

Apply by calling route_model_with_memory() instead of route_model():

  from swarm_os.services.control_plane.router import Router
  from swarm_os.services.control_plane.router_memory_patch import patch_router
  from tools.memory_bridge import MemoryBridge

  bridge = MemoryBridge()
  router = Router(profiles=[...])
  patch_router(router)   # injects route_model_with_memory as a bound method

  decision = await router.route_model_with_memory(
      candidates=["qwen2.5-coder:14b", "qwen2.5-coder:32b"],
      role="fast",
      event_type="WRITE",
      bridge=bridge,
  )
"""
from __future__ import annotations

import time
import types
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from tools.memory_bridge import EventHint, MemoryBridge
    from swarm_os.services.control_plane.router import Router
    from swarm_os.services.control_plane.models import RouteDecision


async def _route_model_with_memory(
    self: "Router",
    *,
    candidates: List[str],
    role: Optional[str] = None,
    event_type: str = "",
    bridge: Optional["MemoryBridge"] = None,
    allow_fallback: bool = True,
) -> "RouteDecision":
    """
    Proactive routing variant that pre-penalises / pre-boosts candidates
    based on EventHints retrieved from the Semantic Memory (Qdrant).

    Decision flow
    -------------
    1. For each candidate model, ask MemoryBridge for an EventHint.
       The hint summarises historical success/failure patterns for the
       (event_type, model) pair, derived from the actual EventLog WAL.
    2. Compose a score:  base role match + reactive failures + memory penalty/boost.
    3. Return the highest-scoring model as a RouteDecision.
       - reason="memory_proactive:…" when hints influenced the choice.
       - reason="scored:…" for pure reactive scoring (no bridge / no evidence).
       - reason="fallback:memory_proactive" when all candidates are blocked.
    """
    from swarm_os.services.control_plane.models import RouteDecision

    # Graceful degradation: no bridge → standard reactive routing
    if bridge is None:
        return self.route_model(
            candidates=candidates, role=role, allow_fallback=allow_fallback
        )

    role = role or self.default_role
    now  = time.time()

    # ── Step 1: gather EventHints concurrently ──────────────────────────────
    import asyncio
    hint_tasks = {
        model: bridge.query_event_hint(event_type=event_type, model=model)
        for model in candidates
    }
    hints: dict[str, "EventHint"] = {}
    for model, coro in hint_tasks.items():
        try:
            hints[model] = await coro
        except Exception:
            pass  # network blip: no hint for this model

    # ── Step 2: composite scoring ───────────────────────────────────────────
    def composite(name: str) -> float:
        profile = self.profiles.get(name)
        state   = self.get_state(name)

        if state.cooldown_until > now:
            return -1e9

        score = 0.0
        if profile and profile.role == role:
            score += 100.0
        elif profile and profile.role == "fast" and role == "fast":
            score += 50.0

        # Reactive: existing failure history
        if state.failures > 0:
            score -= min(50.0, state.failures * 5.0)

        # Proactive: memory hint
        hint = hints.get(name)
        if hint and hint.evidence_count > 0:
            if hint.suggested_avoid:
                # Scale penalty by failure_rate and evidence weight (capped at 5)
                evidence_weight = min(hint.evidence_count, 5)
                score -= 40.0 * hint.failure_rate * evidence_weight
            if hint.suggested_prefer == name:
                score += 20.0

        return score

    ranked = sorted(candidates, key=composite, reverse=True)
    best   = ranked[0]
    best_score = composite(best)

    if best_score > -1e8:
        hint = hints.get(best)
        reason = (
            "memory_proactive"
            if (hint and hint.evidence_count > 0)
            else "scored"
        )
        return RouteDecision(
            model   = best,
            role    = self.get_state(best).role,
            reason  = f"{reason}:{role}",
            fallback= False,
            metadata= {"hint": vars(hint) if hint else {}},
        )

    # All blocked or negative
    if allow_fallback and ranked:
        return RouteDecision(
            model   = best,
            role    = self.get_state(best).role,
            reason  = "fallback:memory_proactive",
            fallback= True,
        )

    return RouteDecision(
        model   = candidates[0] if candidates else "",
        role    = self.default_role,
        reason  = "no_candidates",
        fallback= True,
    )


def patch_router(router: "Router") -> None:
    """
    Inject route_model_with_memory() as a bound async method on an existing
    Router instance.  Call once after constructing the Router.

    Example
    -------
      router = Router(profiles=[...])
      patch_router(router)
      decision = await router.route_model_with_memory(...)
    """
    router.route_model_with_memory = types.MethodType(  # type: ignore[attr-defined]
        _route_model_with_memory, router
    )
