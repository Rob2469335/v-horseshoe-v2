class AnomalyTracker:
    def __init__(self):
        self.items = []

    def record(self, source, level, reason, payload=None):
        item = {
            "source": source,
            "level": level,
            "reason": reason,
            "payload": payload or {}
        }
        self.items.append(item)
        return item

    def list(self):
        return list(self.items)
