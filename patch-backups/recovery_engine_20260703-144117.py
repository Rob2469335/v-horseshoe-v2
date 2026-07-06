class RecoveryEngine:
    def __init__(self, actions=None):
        self.actions = actions or {}

    async def recover(self, anomaly):
        source = anomaly.get("source")
        action = self.actions.get(source)
        if callable(action):
            return await action(anomaly) if hasattr(action, "__await__") else action(anomaly)
        return {"ok": False, "reason": f"no recovery action for {source}"}
