from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class RepairVerifier:
    def __init__(self, probe, chat_adapter: Any | None = None) -> None:
        self.probe = probe
        self.chat_adapter = chat_adapter

    def verify(self, component: str) -> dict[str, object]:
        if component == 'chat_model' and self.chat_adapter:
            # try a quick generate to verify provider
            try:
                res = self.chat_adapter.generate('health-check', retries=0)
                return {
                    'component': component,
                    'verified': bool(res.ok),
                    'status': 'healthy' if res.ok else 'unhealthy',
                    'latency_ms': 0.0,
                    'detail': getattr(res, 'content', '')
                }
            except Exception as e:
                return {'component': component, 'verified': False, 'status': 'unhealthy', 'latency_ms': 0.0, 'detail': str(e)}

        if not self.probe:
            return {"component": component, "verified": True, "status": "healthy", "latency_ms": 0.0, "detail": "no probe"}
        report = self.probe.probe(component)
        return {
            "component": component,
            "verified": getattr(report, 'status', 'healthy') == 'healthy',
            "status": getattr(report, 'status', 'healthy'),
            "latency_ms": getattr(report, 'latency_ms', 0.0),
            "detail": getattr(report, 'detail', ''),
        }
