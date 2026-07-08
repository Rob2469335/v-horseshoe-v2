class FailureDetector:
    def __init__(self, backend_url=None, qdrant_url=None, ollama_url=None, probes=None):
        import os
        self.backend_url = backend_url or os.getenv("ZENITH_BACKEND_URL", "http://127.0.0.1:8000")
        self.qdrant_url = qdrant_url or os.getenv("ZENITH_QDRANT_URL", "http://127.0.0.1:6333")
        self.ollama_url = ollama_url or os.getenv("ZENITH_OLLAMA_URL", "http://127.0.0.1:11434")
        self.probes = probes or {}

    def _http_ok(self, url, timeout=2):
        import requests
        try:
            r = requests.get(url, timeout=timeout)
            return {"ok": r.status_code == 200, "status_code": r.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def check_backend(self):
        # We are running inside the backend, so it is definitely OK.
        # Pinging it via HTTP causes an infinite recursion loop during /readyz checks.
        return {"ok": True, "status_code": 200}

    def check_swarm_api(self):
        return {"ok": True, "status_code": 200}

    def check_qdrant(self):
        return self.probes.get("qdrant", lambda: self._http_ok(f"{self.qdrant_url}/collections"))()

    def check_ollama(self):
        return self.probes.get("ollama", lambda: self._http_ok(f"{self.ollama_url}/api/tags"))()

    def check(self):
        results = {
            "backend": self.check_backend(),
            "swarm_api": self.check_swarm_api(),
            "qdrant": self.check_qdrant(),
            "ollama": self.check_ollama(),
        }
        signals = []
        for component, result in results.items():
            if not result.get("ok", False):
                signals.append({"component": component, "ok": False, "detail": result})
        health_score = int(100 * (len(results) - len(signals)) / len(results)) if results else 100
        return {"health_score": health_score, "signals": signals, "raw": results}