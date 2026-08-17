from __future__ import annotations


class ChaosDrillService:
    def summary(self) -> dict:
        drills = [
            {
                "name": "vector_store_failure",
                "description": "Simulate vector store outage",
            },
            {
                "name": "chat_model_rotate",
                "description": "Rotate chat model provider under failure",
            },
        ]
        coverage_components = ["vector_store", "chat_model"]
        return {
            "status": "ok",
            "total_drills": len(drills),
            "drills": drills,
            "coverage_components": coverage_components,
        }
