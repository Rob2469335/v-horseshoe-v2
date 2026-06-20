# swarm_os/adaptation/healing/healing_engine.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

class HealingAction:
    def __init__(self, action: str, reason: str) -> None:
        self.action = action
        self.reason = reason

class HealingEngine:
    def __init__(
        self,
        state_path: Path | str | None = None,
        executor: Any = None,
        verifier: Any = None,
        metrics: Any = None,
        audit: Any = None,
        policy_engine: Any = None,
        approval_queue: Any = None,
        escalation: Any = None,
        learning: Any = None,
    ) -> None:
        self.state_path = Path(state_path) if state_path is not None else None
        self.executor = executor
        self.verifier = verifier
        self.metrics = metrics
        self.audit = audit
        self.policy_engine = policy_engine
        self.approval_queue = approval_queue
        self.escalation = escalation
        self.learning = learning

        self.state: Dict[str, Any] = {"attempts": {}}
        self._load_state()

    def _load_state(self) -> None:
        if self.state_path and self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as fh:
                    self.state = json.load(fh)
            except Exception:
                pass

    def _save_state(self) -> None:
        if self.state_path:
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.state_path, "w", encoding="utf-8") as fh:
                    json.dump(self.state, fh, default=str)
            except Exception:
                pass

    def plan(self, health_state: dict[str, str]) -> HealingAction:
        status = health_state.get("status", "unknown")
        if status == "healthy":
            return HealingAction(action="noop", reason="system healthy")
        return HealingAction(action="investigate", reason=f"health status is {status}")

    def execute(self, symptom: Dict[str, Any]) -> Dict[str, Any]:
        component = symptom.get("component", "system")
        status = symptom.get("status", "unknown")

        # Get attempt count for this component
        attempts = self.state.get("attempts", {}).get(component, 0)

        # Determine the action to take
        actions = {
            "vector_store": ["restart_vector_layer", "switch_to_fallback_search", "cooldown"],
            "chat_model": ["retry_request", "rotate_model_provider", "cooldown"],
            "system": ["restart_component", "cooldown"],
        }
        component_actions = actions.get(component, ["restart_component", "cooldown"])

        if attempts >= len(component_actions):
            action = "cooldown"
        else:
            action = component_actions[attempts]

        # Increment attempt count
        if "attempts" not in self.state:
            self.state["attempts"] = {}
        self.state["attempts"][component] = attempts + 1
        self._save_state()

        # Policy Gating check
        permitted = True
        reasons = []
        if self.policy_engine:
            policy_res = self.policy_engine.evaluate(component=component, action=action)
            permitted = policy_res.get("permitted", True)
            reasons = policy_res.get("reasons", [])

        if not permitted:
            req = None
            if self.approval_queue:
                req = self.approval_queue.create_request(
                    component=component,
                    action=action,
                    reason="policy gated: " + ", ".join(reasons)
                )
            
            # Record policy block in metrics if metrics configured
            if self.metrics:
                # Gated: not executed, not verified, not escalated (yet)
                self.metrics.record(
                    component=component,
                    action=action,
                    executed=False,
                    verified=False,
                    escalated=False
                )
            if self.audit:
                self.audit.record({
                    "component": component,
                    "action": action,
                    "executed": False,
                    "repair": {"status": "approval_required", "detail": "policy gated"},
                    "verification": {"verified": False, "detail": "skipped"},
                    "escalated": False,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

            res = {
                "action": action,
                "executed": False,
                "repair": {"status": "approval_required"},
                "policy": {"permitted": permitted, "reasons": reasons}
            }
            if req:
                res["approval_request"] = req
            return res

        # If action is cooldown or no executor configured, skip repair
        if action == "cooldown" or not self.executor:
            return {
                "action": action,
                "executed": False,
                "repair": {"status": "skipped"},
                "policy": {"permitted": permitted, "reasons": reasons}
            }

        executed = True
        repair_status = "success"
        repair_detail = "executed"

        # Execute repair
        try:
            exec_res = self.executor.execute(component, action)
            if isinstance(exec_res, tuple):
                success, detail = exec_res
            else:
                success = getattr(exec_res, "status", "success") == "success"
                detail = getattr(exec_res, "detail", str(exec_res))
            
            if not success:
                repair_status = "failed"
                repair_detail = detail
        except Exception as e:
            repair_status = "failed"
            repair_detail = str(e)

        # Verification
        verified = True
        verification_detail = "verified"
        if self.verifier:
            try:
                verify_res = self.verifier.verify(component)
                if isinstance(verify_res, dict):
                    verified = verify_res.get("verified", True)
                    verification_detail = verify_res.get("detail", "ok")
                else:
                    verified = bool(verify_res)
            except Exception as e:
                verified = False
                verification_detail = str(e)

        # Escalation
        escalated = False
        escalation_res = None
        if not verified and self.escalation:
            escalation_res = self.escalation.escalate(
                component=component,
                action=action,
                detail=f"Verification failed: {verification_detail}"
            )
            escalated = True

        # Record metrics
        if self.metrics:
            self.metrics.record(
                component=component,
                action=action,
                executed=executed,
                verified=verified,
                escalated=escalated
            )

        # Record audit
        if self.audit:
            self.audit.record({
                "component": component,
                "action": action,
                "executed": executed,
                "repair": {"status": repair_status, "detail": repair_detail},
                "verification": {"verified": verified, "detail": verification_detail},
                "escalated": escalated,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        # Record learning
        if self.learning:
            self.learning.record_repair(
                component=component,
                action=action,
                success=(repair_status == "success" and verified)
            )

        result = {
            "action": action,
            "executed": executed,
            "repair": {
                "status": repair_status,
                "detail": repair_detail
            },
            "verification": {
                "verified": verified,
                "detail": verification_detail
            },
            "policy": {"permitted": permitted, "reasons": reasons}
        }
        if escalated:
            result["escalation"] = escalation_res
        return result
