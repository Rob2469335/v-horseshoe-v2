from swarm_os.memory.sync.ssrg_memory import SSRGMemory

class CompetitionLayer:
    def __init__(self):
        self.history = []
        self.ssrg = SSRGMemory()

    def _safe_fallback(self, task):
        return ("null_skill", 0.0, {"error": "no_candidates", "task": str(task)})

    def _score(self, skill, base_score):
        """SSRG-influenced scoring"""
        base = float(base_score)

        bias = self.ssrg.get_bias(skill)

        # blend base performance + historical success
        return base * 0.7 + bias * 0.3

    def compete(self, candidates, task):
        if not candidates:
            result = self._safe_fallback(task)
            self.ssrg.record(task, result[0], result[1], result[2])
            return result

        scored = []

        for skill, score in candidates:
            adjusted = self._score(skill, score)
            scored.append((skill, adjusted))

        best_skill, best_score = max(scored, key=lambda x: x[1])

        meta = {
            "source": "competition_v3",
            "candidate_count": len(scored)
        }

        result = (best_skill, best_score, meta)

        self.history.append({
            "task": task,
            "winner": str(best_skill)
        })

        self.ssrg.record(task, best_skill, best_score, meta)

        return result
