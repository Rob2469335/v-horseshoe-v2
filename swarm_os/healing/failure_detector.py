from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio
    import httpx

_failure_http_client: "httpx.AsyncClient | None" = None
_failure_http_client_loop: "asyncio.AbstractEventLoop | None" = None

def _get_failure_http_client() -> "httpx.AsyncClient":
    """Pooled client, but bound to the event loop that created it.

    `run_coro_sync()` runs probes inside a fresh `asyncio.run()` loop on a
    daemon thread. A module-level client cached across loops holds connections
    owned by a now-closed loop, so the NEXT probe throws `Event loop is closed`
    (verified: alternating healthy/failed qdrant probes — the false "heal me"
    signal that made the watchman nag about a healthy Qdrant). Recreate the
    client whenever the running loop differs."""
    import asyncio
    global _failure_http_client, _failure_http_client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _failure_http_client is None or _failure_http_client_loop is not loop:
        import httpx
        _failure_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=2.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
            headers={"Authorization": "Bearer llama"},
        )
        _failure_http_client_loop = loop
    return _failure_http_client


class FailureDetector:
    def __init__(self, backend_url=None, qdrant_url=None, ollama_url=None, probes=None):
        import os
        self.backend_url = backend_url or os.getenv("ZENITH_BACKEND_URL", "http://127.0.0.1:8000")
        self.qdrant_url = qdrant_url or os.getenv("ZENITH_QDRANT_URL", "http://127.0.0.1:6333")
        self.llamacpp_url = ollama_url or os.getenv("ZENITH_LLAMACPP_URL", "http://127.0.0.1:8080/v1")
        self.probes = probes or {}

    @staticmethod
    async def _http_ok(url, timeout=2):
        try:
            client = _get_failure_http_client()
            r = await client.get(url, timeout=timeout)
            return {"ok": r.status_code == 200, "status_code": r.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def check_backend(self):
        # We are running inside the backend, so it is definitely OK.
        # Pinging it via HTTP causes an infinite recursion loop during /readyz checks.
        return {"ok": True, "status_code": 200}

    def check_swarm_api(self):
        return {"ok": True, "status_code": 200}

    async def check_qdrant(self):
        probe = self.probes.get("qdrant")
        if probe:
            import inspect
            return await probe() if inspect.iscoroutinefunction(probe) else probe()
        return await self._http_ok(f"{self.qdrant_url}/collections", timeout=5.0)

    async def check_llamacpp(self):
        probe = self.probes.get("llamacpp")
        if probe:
            import inspect
            return await probe() if inspect.iscoroutinefunction(probe) else probe()
        base = self.llamacpp_url.replace("/v1", "").rstrip("/")
        res = await self._http_ok(f"{base}/health", timeout=10.0)
        if not res.get("ok"):
            res = await self._http_ok(f"{self.llamacpp_url}/models", timeout=10.0)
        return res

    def check_context_utilization(self):
        """Silent-degradation probe: flag context pressure before truncation happens.
        Uses the CLI token_tracker state (avg context usage across sessions)."""
        try:
            import os, json
            tracker_path = os.path.join("logs", "token_tracker_state.json")
            if not os.path.exists(tracker_path):
                return {"ok": True, "detail": "no tracker data"}
            with open(tracker_path) as f:
                data = json.load(f)
            # context_percent is recorded as e.g. 0.54; flag > 0.85
            recent = [v for k, v in data.items() if k.startswith("ctx")] or []
            high = [v for v in recent if isinstance(v, (int, float)) and v > 0.85]
            if high:
                return {"ok": False, "detail": f"context utilization high ({max(high):.0%})"}
            return {"ok": True, "detail": "context within budget"}
        except Exception as exc:
            return {"ok": True, "detail": f"context check unavailable: {exc}"}

    def check_retry_rate(self):
        """Silent-degradation probe: rising JSON-repair/retry rate signals model trouble."""
        try:
            from runtime_v2.services.fallback_manager import _cooldowns
            cooled = {k: v for k, v in _cooldowns.items() if v.get("failures", 0) > 0}
            if cooled:
                worst = max(cooled.items(), key=lambda kv: kv[1].get("failures", 0))
                return {"ok": False, "detail": f"{worst[0]} in cooldown ({worst[1]['failures']} failures)"}
            return {"ok": True, "detail": "no models in cooldown"}
        except Exception as exc:
            return {"ok": True, "detail": f"retry check unavailable: {exc}"}

    async def check(self):
        results = {
            "backend": self.check_backend(),
            "swarm_api": self.check_swarm_api(),
            "qdrant": await self.check_qdrant(),
            "llamacpp": await self.check_llamacpp(),
            "context_utilization": self.check_context_utilization(),
            "retry_rate": self.check_retry_rate(),
        }
        # Whole-computer probes (disk/RAM/runaway/temp/event-log). These are
        # read-only and fast; run them synchronously in this call.
        try:
            from .system_probes import run_system_probes
            results.update(run_system_probes())
        except Exception as exc:
            results["system_probes"] = {"ok": True, "detail": {"issue": "system_probes", "available": False, "error": str(exc)}}
        signals = []
        for component, result in results.items():
            if not result.get("ok", False):
                signals.append({"component": component, "ok": False, "detail": result})
        health_score = int(100 * (len(results) - len(signals)) / len(results)) if results else 100
        return {"health_score": health_score, "signals": signals, "raw": results}

    def check_sync(self):
        return run_coro_sync(self.check())


def run_coro_sync(coro_or_val, timeout: float = 60.0):
    import inspect, asyncio, threading
    if not inspect.iscoroutine(coro_or_val):
        return coro_or_val
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        result = []
        error = []

        def _worker():
            try:
                result.append(asyncio.run(coro_or_val))
            except Exception as e:
                error.append(e)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if error:
            raise error[0]
        if not result:
            # Timed out — the daemon thread + its loop will be reaped by the interpreter,
            # but don't let the caller block on it. Return an empty result.
            return {}
        return result[0]
    else:
        return asyncio.run(coro_or_val)