# swarm_os/tests/unit/test_selection.py
"""Updated to use new Genome dataclass and kernel import paths."""
import unittest
import random
from swarm_os.kernel.genetics import Genome, normalize_affinities
from swarm_os.kernel.organism import Organism
from swarm_os.kernel.selection import SelectionEngine, score_response


def _make_org(org_id: str, fitness: float = 0.0) -> Organism:
    """Create organism without touching disk — no mocking needed."""
    g = Genome()
    normalize_affinities(g)
    def brain(ctx):
        return {"content": "test", "elapsed": 1.0,
                "finish_reason": "stop", "cost": 0.1,
                "tools_used": [], "model": "test", "total_tokens": 10}
    # Create organism directly, bypass MemoryBank disk writes
    org = object.__new__(Organism)
    org.id = org_id
    org.brain = brain
    org.genome = g
    org.fitness = fitness
    org._action_count = 0
    # Stub memory so writes are no-ops
    class _NullMemory:
        def write(self, *a, **kw): pass
        def recent(self, n=20): return []
    org.memory = _NullMemory()
    g.record_fitness(fitness)
    return org


class TestScoreResponse(unittest.TestCase):
    def test_good_coding_response(self):
        g = Genome()
        action = {"content": "```python\ndef f(): pass\n```\nSolid answer.",
                  "elapsed": 5.0, "finish_reason": "stop", "error": None}
        scores = score_response(action, g, "coding")
        self.assertGreater(scores["quality"], 0)
        self.assertGreater(scores["composite"], 0)

    def test_error_response_zero(self):
        g = Genome()
        scores = score_response({"error": "timeout", "content": ""}, g, "coding")
        self.assertEqual(scores["composite"], 0.0)

    def test_upwork_response_scores_fields(self):
        g = Genome()
        action = {"content": "Budget: $500. Client rating: 4.8. Fit score: 8/10. Recommend: Apply.",
                  "elapsed": 3.0, "finish_reason": "stop", "error": None}
        scores = score_response(action, g, "upwork")
        self.assertGreater(scores["quality"], 0.3)


class TestSelectionEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SelectionEngine()
        self.orgs = [
            _make_org("A", 0.1),
            _make_org("B", 0.5),
            _make_org("C", 0.9),
        ]

    def test_softmax_sums_to_one(self):
        w = self.engine.softmax(self.orgs)
        self.assertAlmostEqual(sum(w), 1.0, places=6)
        self.assertEqual(len(w), 3)

    def test_softmax_best_has_highest_weight(self):
        w = self.engine.softmax(self.orgs)
        self.assertGreater(w[2], w[0])

    def test_select_returns_correct_count(self):
        random.seed(3)
        picked = self.engine.select(self.orgs, k=5)
        self.assertEqual(len(picked), 5)
        self.assertTrue(all(o in self.orgs for o in picked))

    def test_cull_limits_and_sorts(self):
        culled = self.engine.cull(list(self.orgs), max_size=2)
        self.assertEqual(len(culled), 2)
        self.assertGreaterEqual(
            culled[0].genome.average_fitness,
            culled[1].genome.average_fitness,
        )

    def test_top_organisms(self):
        top = self.engine.top_organisms(self.orgs, n=2)
        self.assertEqual(len(top), 2)
        self.assertGreaterEqual(
            top[0].genome.average_fitness,
            top[1].genome.average_fitness,
        )


if __name__ == "__main__":
    unittest.main()
