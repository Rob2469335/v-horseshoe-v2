class FailureDetector:
    def __init__(self, probes=None):
        self.probes = probes or {}

    def check(self):
        return {
            "orchestrator": self.probes.get("orchestrator", lambda: {"ok": True})(),
            "qdrant": self.probes.get("qdrant", lambda: {"ok": True})(),
            "ollama": self.probes.get("ollama", lambda: {"ok": True})(),
            "api": self.probes.get("api", lambda: {"ok": True})(),
        }
