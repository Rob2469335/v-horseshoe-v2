from __future__ import annotations


class RunbookService:
    def __init__(self) -> None:
        pass

    def get_runbook(self, component: str) -> dict:
        return {
            "component": component,
            "automated_actions": ["rotate_model_provider", "restart_vector_layer"],
            "manual_checks": ["check_provider_status", "review_logs"],
        }
