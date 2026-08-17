from __future__ import annotations

from typing import List, Dict, Any, Optional
from .governor_models import gen_id
import asyncio


class Diagnostician:
    """Diagnoses symptoms into ranked hypotheses.

    Each hypothesis carries a `fix_class`:
      - "prompt_sensitivity" (PS): fixed by instruction/rule changes → sandbox repair script
      - "model_variability" (MV): the model itself can't do it → escalate to cloud/human
    This mirrors the PS/MV classification used by 2025 self-healing agent research,
    letting the Governor route to the right recovery path instead of guessing.
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

    def _classify_fix(self, text: str) -> str:
        """Classify failure as prompt_sensitivity vs model_variability.

        PS indicators: explicit rule violations, format errors, missing fields,
        JSON/markdown issues, "should not", "forbidden", spec drift → fixable by
        prompt/rule changes.
        MV indicators: hallucination, reasoning failure, "i cannot", repeated
        wrong answers with correct instructions, inherent model limitation →
        retry/reflection is wasted, escalate instead.
        """
        ps_terms = [
            "json",
            "format",
            "invalid",
            "malformed",
            "forbidden",
            "unauthorized",
            "not allowed",
            "missing field",
            "syntax error",
            "parse",
            "schema",
            "expected",
            "must be",
            "should not",
            "violation",
            "rule",
            "constraint",
        ]
        mv_terms = [
            "hallucin",
            "cannot",
            "unable to",
            "don't know",
            "doesn't know",
            "i don't know",
            "reasoning",
            "confus",
            "wrong answer",
            "misunderstanding",
            "nonsense",
            "not capable",
            "out of scope",
        ]
        if any(t in text for t in mv_terms):
            return "model_variability"
        if any(t in text for t in ps_terms):
            return "prompt_sensitivity"
        return "prompt_sensitivity"  # default: cheaper to try a rule/script fix first

    def diagnose(self, symptom: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Basic heuristics: if symptom mentions 'timeout' prefer network issues, if 'OOM' prefer memory
        text = str(symptom.get("detail") or symptom.get("message") or symptom).lower()
        fix_class = self._classify_fix(text)
        hypotheses: List[Dict[str, Any]] = []

        if "timeout" in text or "timed out" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "network_timeout",
                    "confidence": 0.6,
                    "explanation": "Requests timed out — network or upstream may be slow.",
                    "fix_class": fix_class,
                }
            )
        if "out of memory" in text or "oom" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "memory_pressure",
                    "confidence": 0.75,
                    "explanation": "Process likely ran out of memory.",
                    "fix_class": fix_class,
                }
            )
        if "connection refused" in text or "refused" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "service_unreachable",
                    "confidence": 0.7,
                    "explanation": "A downstream service refused the connection.",
                    "fix_class": fix_class,
                }
            )
        if "json" in text or "parse" in text or "format" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "format_violation",
                    "confidence": 0.8,
                    "explanation": "Output violated expected format — fixable via prompt/rule tightening.",
                    "fix_class": "prompt_sensitivity",
                }
            )
        if "delegate" in text or "circular" in text or "chain" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "delegation_loop",
                    "confidence": 0.7,
                    "explanation": "Agent tried to re-delegate to an already-visited agent.",
                    "fix_class": "prompt_sensitivity",
                }
            )

        # Whole-computer signals: probe detail carries the issue name, so the
        # diagnose text is e.g. "{'issue': 'memory_pressure', ...}". Give the
        # safe issues (memory pressure, temp growth) high confidence so they
        # auto-run; destructive ones are still forced to approval by the governor.
        if "memory_pressure" in text or "ram_percent" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "memory_pressure",
                    "confidence": 0.9,
                    "explanation": "System RAM utilization is high — empty working sets of non-critical processes.",
                    "fix_class": "prompt_sensitivity",
                }
            )
        if "disk_space" in text or "free_gb" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "disk_space",
                    "confidence": 0.9,
                    "explanation": "A drive is near-full — clean stale temp files.",
                    "fix_class": "prompt_sensitivity",
                }
            )
        if "runaway_process" in text or "cpu_percent" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "runaway_process",
                    "confidence": 0.9,
                    "explanation": "A process is pegged at high CPU/RAM — terminate it after safety checks.",
                    "fix_class": "prompt_sensitivity",
                }
            )
        if "temp_growth" in text or "temp_gb" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "temp_growth",
                    "confidence": 0.9,
                    "explanation": "OS temp folder is ballooning — remove stale files.",
                    "fix_class": "prompt_sensitivity",
                }
            )
        if "event_log_storm" in text or "errors" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "event_log_storm",
                    "confidence": 0.7,
                    "explanation": "Recent error storm in the Windows Event Log — investigate, no auto-remedy.",
                    "fix_class": "model_variability",
                }
            )
        if "stopped_service" in text or "service_name" in text:
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "stopped_service",
                    "confidence": 0.9,
                    "explanation": "A critical Windows service stopped — restart it.",
                    "fix_class": "prompt_sensitivity",
                }
            )

        # Qdrant similarity lookup: if configured, search for similar incidents and boost matching hypothesis confidences
        try:
            q_results = self._call_qdrant(text, top_k=5)
            if q_results:
                # each result payload may contain a 'payload' dict or the stored structure; adapt
                # boost factor proportional to top match score
                top_score = max((r.get("score", 0) or 0) for r in q_results)
                boost = min(0.2, top_score * 0.1)
                for h in hypotheses:
                    h["confidence"] = min(0.98, h.get("confidence", 0.5) + boost)
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
            hypotheses.append(
                {
                    "id": gen_id("h"),
                    "hypothesis": "unknown",
                    "confidence": 0.3,
                    "explanation": "Insufficient data — require deeper probes.",
                    "fix_class": fix_class,
                }
            )

        # sort by confidence desc
        hypotheses.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return hypotheses
