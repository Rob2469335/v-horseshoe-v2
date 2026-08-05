class RollbackManager:
    def __init__(self, snapshot_repo=None):
        self.snapshot_repo = snapshot_repo

    def latest_snapshot(self):
        if self.snapshot_repo and hasattr(self.snapshot_repo, "latest_snapshot"):
            return self.snapshot_repo.latest_snapshot()
        return None

    def restore(self, snapshot):
        if self.snapshot_repo and hasattr(self.snapshot_repo, "restore"):
            return self.snapshot_repo.restore(snapshot)
        return {"ok": False, "reason": "rollback unavailable"}
