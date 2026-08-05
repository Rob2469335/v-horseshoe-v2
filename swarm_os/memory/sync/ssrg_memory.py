class SSRGMemory:
    def __init__(self):
        self.events = []

    def record(self, task, winner, score, meta):
        self.events.append({
            "task": str(task),
            "winner": str(winner),
            "score": float(score),
            "meta": meta or {}
        })

    def get_bias(self, candidate):
        """Return historical success bias for a candidate"""
        count = 0
        total = 0.0

        for e in self.events:
            if e["winner"] == str(candidate):
                count += 1
                total += e["score"]

        if count == 0:
            return 0.0

        return total / count

    def last(self, n=10):
        return self.events[-n:]
