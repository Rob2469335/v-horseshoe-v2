from __future__ import annotations

from typing import Dict, Any, Optional
from .diagnostician import Diagnostician
from .learner import Learner
from .governor_models import gen_id, FailureRecord


class Governor:
    """Governed decision maker that uses diagnositician, policy engine, and learner to decide
    on an action: auto, sandbox, approval_required, or reject.
    """

    def __init__(self, diagnostician: Optional[Diagnostician] = None, policy_engine: Optional[object] = None, learner: Optional[Learner] = None, strategy_stats: Optional[dict] = None, strategy_registry: Optional[object] = None):
        self.diagnostician = diagnostician or Diagnostician(memory=(learner.learning if learner else None))
        self.policy_engine = policy_engine
        self.learner = learner or Learner()
        # strategy stats map: id -> config (approval_required_threshold etc.)
        self.strategy_stats = strategy_stats or {}
        self._strategy_registry = strategy_registry

    def decide(self, symptom: Dict[str, Any]) -> Dict[str, Any]:
        incident_id = gen_id("inc")
        hypotheses = self.diagnostician.diagnose(symptom)
        top_conf = hypotheses[0].get("confidence", 0.0) if hypotheses else 0.0

        # policy check for actions to be considered
        # naive default: compute a risk score (1 - confidence) and compare to thresholds
        # if low confidence -> require approval; if high -> auto
        decision = {"incident_id": incident_id, "hypotheses": hypotheses}

        # consult policy engine if available to see if particular action types are gated
        try:
            if self.policy_engine:
                # ask policy for recommended mode; fallback to confidence-based
                pol = self.policy_engine.evaluate_policy_for_symptom(symptom)
                if pol:
                    decision["policy"] = pol
                    if pol.get("action") == "reject":
                        decision["mode"] = "reject"
                        return decision
                    if pol.get("action") == "require_approval":
                        decision["mode"] = "approval_required"
                        return decision
        except Exception:
            pass

        # confidence thresholds, may be overridden per strategy
        approval_threshold = 0.5
        auto_threshold = 0.85

        # strategy_stats (written by finalize()): proven win-rates influence autonomy.
        # Repeatedly successful strategies earn trust (lower the bar to auto-execute);
        # failing ones demand oversight (raise it). Only consult meaningful samples.
        strat_win_rate = None
        try:
            comp = symptom.get("component")
            if comp:
                if self._strategy_registry is None:
                    from .strategy_registry import StrategyRegistry
                    self._strategy_registry = StrategyRegistry()
                all_stats = self._strategy_registry.list_all()
                entries = {k: v for k, v in all_stats.items() if k.startswith(f"{comp}:")}
                if entries:
                    total_succ = sum(e.get("success_count", 0) for e in entries.values())
                    total_fail = sum(e.get("failure_count", 0) for e in entries.values())
                    total = total_succ + total_fail
                    if total >= 5:
                        strat_win_rate = total_succ / total
        except Exception:
            strat_win_rate = None
        decision["strategy_win_rate"] = strat_win_rate
        forced_approval = False
        if strat_win_rate is not None:
            if strat_win_rate >= 0.8:
                auto_threshold = 0.6
                approval_threshold = 0.3
            elif strat_win_rate < 0.4:
                # A strategy that historically fails must not auto-execute even
                # under a confident diagnosis — demand human oversight.
                forced_approval = True

        if forced_approval:
            decision["mode"] = "approval_required"
            decision["mode_reason"] = f"low strategy win-rate ({strat_win_rate:.0%}) for component — human oversight required"
        elif top_conf >= auto_threshold:
            decision["mode"] = "auto_execute"
        elif top_conf < approval_threshold:
            decision["mode"] = "approval_required"
        else:
            decision["mode"] = "sandbox_first"

        # persist a pre-execution failure record with placeholders
        fr = FailureRecord(
            incident_id=incident_id,
            symptom=symptom,
            root_cause=None,
            hypotheses=hypotheses,
            repair_attempts=[],
            successful_fix=None,
            confidence=top_conf,
            outcome="OPEN",
            service=symptom.get("component"),
            environment=symptom.get("environment", {}),
            metrics_before=symptom.get("metrics_before", {}),
            metrics_after={},
        )
        try:
            self.learner.persist_failure(fr)
        except Exception:
            pass

        return decision

    def finalize(self, incident_id: str, outcome: Dict[str, Any]):
        """Finalize an incident: update failure record outcome and persist reflection, update strategy stats."""
        # basic implementation: find the failure in learner store and update
        try:
            failures = self.learner.list_failures()
            for f in failures:
                if f.get("incident_id") == incident_id:
                    f["outcome"] = outcome.get("outcome", "UNKNOWN")
                    f["metrics_after"] = outcome.get("metrics_after", {})
                    # update success/failure
                    if outcome.get("outcome") == "SUCCESS":
                        f["successful_fix"] = outcome.get("repair")
                        f["confidence"] = outcome.get("confidence", f.get("confidence", 0.0))
                    # write back
                    # for simplicity learner.persist_failure inserts a new item; we directly save cache
                    if hasattr(self.learner, "_cache") and self.learner._cache is not None:
                        # replace matching entry
                        lst = self.learner._cache.get("failures", [])
                        for i,entry in enumerate(lst):
                            if entry.get("incident_id") == incident_id:
                                lst[i] = f
                                break
                        self.learner._cache["failures"] = lst
                        self.learner._save_cache()
                    break
        except Exception:
            pass

        # update strategy stats if provided
        try:
            from .strategy_registry import StrategyRegistry
            # naive strategy id based on component+repair action
            strat = StrategyRegistry()
            comp = outcome.get('repair', {}).get('component') if outcome.get('repair') else None
            if not comp:
                comp = outcome.get('component')
            if not comp:
                # try to find service from stored failure
                try:
                    failures = self.learner.list_failures()
                    match = next((x for x in failures if x.get('incident_id') == incident_id), None)
                    if match:
                        comp = match.get('service')
                except Exception:
                    comp = None
            action = outcome.get('repair', {}).get('action') if outcome.get('repair') else None
            if comp and action:
                strategy_id = f"{comp}:{action}"
                success = outcome.get('outcome') == 'SUCCESS'
                duration = outcome.get('duration', 0.0) or 0.0
                strat.update(strategy_id, success=success, duration=duration)
        except Exception:
            pass
