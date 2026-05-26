# swarm_os/tests/unit/test_genetics.py
import unittest
import random
from swarm_os.kernel.genetics import (
    Genome, CognitivePolicy, MCP_TOOL_REGISTRY,
    mutate, crossover, normalize_affinities,
)


class TestGenomeCopy(unittest.TestCase):
    def test_copy_increments_generation(self):
        g = Genome(generation=3, mutation_rate=0.2)
        c = g.copy("parent-1")
        self.assertEqual(c.generation, 4)
        self.assertEqual(c.parent_id, "parent-1")
        self.assertEqual(c.mutation_rate, 0.2)

    def test_copy_resets_fitness(self):
        g = Genome()
        g.record_fitness(0.9)
        c = g.copy("p")
        self.assertEqual(c.evaluations, 0)
        self.assertEqual(c.lifetime_fitness, 0.0)

    def test_copy_deep_copies_cognition(self):
        g = Genome()
        c = g.copy("p")
        c.cognition.decomposition_bias = 0.99
        self.assertNotEqual(g.cognition.decomposition_bias, 0.99)

    def test_copy_deep_copies_tool_genes(self):
        g = Genome()
        c = g.copy("p")
        c.tool_genes["web_search"] = 0.99
        self.assertNotEqual(g.tool_genes["web_search"], 0.99)


class TestCrossover(unittest.TestCase):
    def _make(self, c, r, u):
        g = Genome(coding_affinity=c, research_affinity=r, upwork_affinity=u)
        normalize_affinities(g)
        return g

    def test_child_has_all_tool_genes(self):
        random.seed(1)
        a = self._make(0.8, 0.1, 0.1)
        b = self._make(0.1, 0.8, 0.1)
        child = crossover(a, b)
        self.assertEqual(set(child.tool_genes.keys()), set(MCP_TOOL_REGISTRY))

    def test_child_affinities_sum_to_one(self):
        random.seed(2)
        a = self._make(0.7, 0.2, 0.1)
        b = self._make(0.1, 0.3, 0.6)
        child = crossover(a, b)
        total = child.coding_affinity + child.research_affinity + child.upwork_affinity
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_child_generation_incremented(self):
        a = Genome(generation=3)
        b = Genome(generation=5)
        child = crossover(a, b)
        self.assertEqual(child.generation, 6)


class TestMutate(unittest.TestCase):
    def test_mutation_rate_stays_in_bounds(self):
        random.seed(2)
        g = Genome(mutation_rate=0.1)
        for _ in range(50):
            mutate(g)
        self.assertGreaterEqual(g.mutation_rate, 0.01)
        self.assertLessEqual(g.mutation_rate, 0.4)

    def test_all_genes_clamped(self):
        random.seed(3)
        g = Genome(model_tier=0.99, temperature=0.99)
        for _ in range(20):
            mutate(g)
        self.assertGreaterEqual(g.model_tier, 0.0)
        self.assertLessEqual(g.model_tier, 1.0)

    def test_affinities_normalized_after_mutate(self):
        random.seed(4)
        g = Genome(coding_affinity=0.5, research_affinity=0.3, upwork_affinity=0.2)
        normalize_affinities(g)
        for _ in range(10):
            mutate(g)
        total = g.coding_affinity + g.research_affinity + g.upwork_affinity
        self.assertAlmostEqual(total, 1.0, places=6)


class TestFromDict(unittest.TestCase):
    def test_round_trip(self):
        g = Genome(model_tier=0.7, temperature=0.3)
        g.record_fitness(0.8)
        d  = g.to_dict()
        g2 = Genome.from_dict(d)
        self.assertAlmostEqual(g2.model_tier, 0.7)
        self.assertAlmostEqual(g2.temperature, 0.3)
        self.assertEqual(g2.evaluations, 1)
        self.assertIsInstance(g2.cognition, CognitivePolicy)

    def test_active_tools_seeded(self):
        g = Genome()
        # Set all tool genes to high value so tools are reliably active
        for k in g.tool_genes:
            g.tool_genes[k] = 0.9
        t1 = g.active_tools(seed=42)
        t2 = g.active_tools(seed=42)
        self.assertEqual(t1, t2)
        self.assertGreater(len(t1), 0)


if __name__ == "__main__":
    unittest.main()
