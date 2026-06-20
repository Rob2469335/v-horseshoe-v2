from __future__ import annotations

from typing import List, Dict, Any, Optional
from .governor_models import gen_id
import asyncio


class Diagnostician:
    """Simple diagnositician skeleton: given a symptom and history, return ranked hypotheses.

    This is intentionally lightweight and designed to be extended. It returns a list of
    hypotheses each with a confidence score between 0..1. Optionally integrates with a
    Qdrant-backed semantic search to find similar past incidents and boost confidences.
    """

    def __init__(self, memory=None, qdrant_search_callable: Optional[callable] = None):
        # memory can be LearningService or any object with list_outcomes/get_component_profile
        # qdrant_search_callable: sync function (query:str)->list[dict] or None
        self.memory = memory
        self.qdrant_search = qdrant_search_callable

    def _call_qdrant(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        if not self.qdrant_search:
            return []
        try:
            res = self.qdrant_search(query, top_k=top_k)
            # If the callable returned a coroutine, run it
            if asyncio.iscoroutine(res):
                return asyncio.run(res)
            return res
        except Exception:
            # fallback: attempt to run as coroutine
            try:
                return asyncio.run(self.qdrant_search(query, top_k=top_k))
            except Exception:
                return []

    def diagnose(self, symptom: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Basic heuristics: if symptom mentions 'timeout' prefer network issues, if 'OOM' prefer memory
        text = str(symptom.get("detail") or symptom.get("message") or symptom).lower()
        hypotheses: List[Dict[str, Any]] = []

        if "timeout" in text or "timed out" in text:
            hypotheses.append({"id": gen_id("h"), "hypothesis": "network_timeout", "confidence": 0.6, "explanation": "Requests timed out — network or upstream may be slow."})
        if "out of memory" in text or "oom" in text:
            hypotheses.append({"id": gen_id("h"), "hypothesis": "memory_pressure", "confidence": 0.75, "explanation": "Process likely ran out of memory."})
        if "connection refused" in text or "refused" in text:
            hypotheses.append({"id": gen_id("h"), "hypothesis": "service_unreachable", "confidence": 0.7, "explanation": "A downstream service refused the connection."})

        # Qdrant similarity lookup: if configured, search for similar incidents and boost matching hypothesis confidences
        try:
            q_results = self._call_qdrant(text, top_k=5)
            if q_results:
                # each result payload may contain a 'payload' dict or the stored structure; adapt
                # boost factor proportional to top match score
                top_score = max((r.get('score', 0) or 0) for r in q_results)
                boost = min(0.2, top_score * 0.1)
                for h in hypotheses:
                    h['confidence'] = min(0.98, h.get('confidence', 0.5) + boost)
        except Exception:
            pass

        # Use memory to boost confidence if similar failures recently occurred
        if self.memory:
            try:
                comp = symptom.get("component")
                if comp:
                    profile = self.memory.get_component_profile(comp)
                    recent = profile.get("recent_repairs", [])
                    if recent:
                        # if recent repairs exist, give a small boost to hypothesis confidence
                        for h in hypotheses:
                            h["confidence"] = min(0.95, h.get("confidence", 0.5) + 0.1)
            except Exception:
                pass

        # fallback generic hypothesis
        if not hypotheses:
            hypotheses.append({"id": gen_id("h"), "hypothesis": "unknown", "confidence": 0.3, "explanation": "Insufficient data — require deeper probes."})

        # sort by confidence desc
        hypotheses.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return hypotheses
