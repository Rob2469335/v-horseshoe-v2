import logging
from typing import Any
import psutil
import httpx

_http_client = httpx.AsyncClient(
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=100),
    trust_env=False,
    proxy=None,
)

log = logging.getLogger(__name__)


class SystemService:
    @staticmethod
    async def get_health_report(runtime: Any) -> dict[str, Any]:
        try:
            # Check system resources
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")

            # Check dependencies
            llm_ok = False
            try:
                r = await _http_client.get(
                    "http://127.0.0.1:8080/v1/models",
                    headers={"Authorization": "Bearer llama"},
                    timeout=1.0,
                )
                llm_ok = r.status_code == 200
            except Exception as e:
                log.debug("LLM health check failed: %s", e)

            qdrant_ok = False
            try:
                r = await _http_client.get("http://127.0.0.1:6333/", timeout=1.0)
                qdrant_ok = r.status_code == 200
            except Exception as e:
                log.debug("Qdrant health check failed: %s", e)

            healing = getattr(runtime, "healing", None)
            report = await healing.status() if hasattr(healing, "status") else {}

            health_score = report.get(
                "health_score", report.get("recovery_readiness", 100)
            )
            if not llm_ok or not qdrant_ok or mem.percent > 95:
                # BUG FIX: Clamp to 0 — health_score can't go negative
                health_score = max(0, health_score - 20)

            return {
                "status": "ok" if health_score >= 80 else "degraded",
                "health_score": health_score,
                "overall": report.get(
                    "overall",
                    "active" if report.get("active_anomalies", 0) > 0 else "healthy",
                ),
                "system": {
                    "memory_percent": mem.percent,
                    "disk_percent": disk.percent,
                    "llm_connected": llm_ok,
                    "ollama_connected": llm_ok,  # Backward compatibility for existing UI
                    "qdrant_connected": qdrant_ok,
                },
            }
        except Exception as exc:
            log.exception("Health check failed")
            return {
                "status": "error",
                "health_score": 0,
                "overall": f"health check failed: {exc}",
            }

    @staticmethod
    async def check_llm_reachable() -> bool:
        try:
            r = await _http_client.get(
                "http://127.0.0.1:8080/v1/models",
                headers={"Authorization": "Bearer llama"},
                timeout=15.0,
            )
            return r.status_code == 200
        except Exception as e:
            log.debug("check_llm_reachable failed: %s", e)
            return False

    check_ollama_reachable = check_llm_reachable  # Backward compatibility alias
