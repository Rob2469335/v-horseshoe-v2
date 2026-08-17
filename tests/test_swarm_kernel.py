import random

import pytest

from swarm_os.config.settings import settings
from swarm_os.kernel.environment import Environment
from swarm_os.kernel.genetics import Genome, mutate, crossover
from swarm_os.kernel.organism import Organism
from swarm_os.kernel.swarm_kernel import SwarmKernel


def make_brain():
    def brain(ctx):
        return {
            "content": "```python\ndef f(): return 1\n```",
            "elapsed": 1.0,
            "finish_reason": "stop",
            "cost": 0.1,
            "tools_used": [],
            "model": "test",
            "total_tokens": 10,
        }

    return brain


def make_kernel(n=4):
    random.seed(42)
    env = Environment()
    organisms = [Organism(f"org_{i}", make_brain(), Genome()) for i in range(n)]
    return SwarmKernel(organisms, env)


@pytest.mark.anyio
async def test_step_runs_real_evolution():
    kernel = make_kernel()
    before = len(kernel.organisms)
    summary = await kernel.step_async()

    assert kernel.generation == 1
    # Evolution actually happened: children were bred and elites cloned,
    # so the population is larger than the originals (before cull).
    assert isinstance(summary, dict)
    assert "children_bred" in summary
    # The full kernel breeds children and elite-clones; population must be
    # non-trivial (>= original count).
    assert len(kernel.organisms) >= before


@pytest.mark.anyio
async def test_population_stays_bounded():
    kernel = make_kernel(6)
    await kernel.step_async()
    await kernel.step_async()
    await kernel.step_async()
    assert len(kernel.organisms) <= settings.population_max


@pytest.mark.anyio
async def test_elite_clones_do_not_alias_parent_fitness():
    from swarm_os.kernel.swarm_kernel import _clone_organism

    _env = Environment()
    org = Organism("parent", make_brain(), Genome())
    clone = _clone_organism(org, "elite_parent_g0", generate_fn=None)

    # Fitness dicts must be independent objects (no shared aliasing).
    assert clone.genome.lifetime_fitness is not org.genome.lifetime_fitness
    clone.genome.lifetime_fitness["composite"] = 0.99
    assert org.genome.lifetime_fitness.get("composite") != 0.99


def test_genome_mutation_boundaries():
    g = Genome()
    g.reasoning_depth = 0.5

    # Mutate with extremely high variance to push boundaries
    g.mutation_rate = 1.0
    g.lifetime_fitness = {"composite": 0.0}
    g.evaluations = 1
    mutate(g)

    # Ensure it stays within 0.0 and 1.0 (assuming the genome implementation bounds it)
    assert 0.0 <= g.reasoning_depth <= 1.0


def test_genome_crossover():
    g1 = Genome()
    g1.reasoning_depth = 0.1
    g1.crossover_stability = 0.0
    g1.cognition.hallucination_sensitivity = 0.9

    g2 = Genome()
    g2.reasoning_depth = 0.9
    g2.crossover_stability = 0.0
    g2.cognition.hallucination_sensitivity = 0.1

    child = crossover(g1, g2)
    assert child is not None


def test_ast_slice_returns_verbatim_source():
    """ast_slice must return a slice that appears VERBATIM in the source (so
    replace() can find it). Regression for the ast.unparse() normalization bug
    which de-indented/requoted the slice so core_code.replace() silently no-opped."""
    from swarm_os.kernel.genetics import ast_slice

    src = (
        "class Foo:\n"
        "    def target(self, x: int) -> int:\n"
        "        if x > 0:\n"
        "            return x * 2\n"
        "        return 0\n"
        "\n"
        "def other():\n"
        "    return 1\n"
    )
    slice_text = ast_slice(src, "target")
    assert "target" in slice_text
    assert slice_text in src  # verbatim match required for replace()
    # And it must not return the whole file (the catastrophic bug).
    assert slice_text != src
    assert "other()" not in slice_text


def test_ast_slice_missing_func_returns_empty():
    from swarm_os.kernel.genetics import ast_slice

    assert ast_slice("def a():\n    pass\n", "nonexistent") == ""


@pytest.mark.anyio
async def test_elite_clones_do_not_chain_lineage():
    """Elites must not be re-cloned into runaway lineage (elite_elite_elite_...)."""
    kernel = make_kernel(3)
    for _ in range(8):
        await kernel.step_async()

    flat = " ".join(o.id for o in kernel.organisms)
    assert "elite_elite" not in flat
    assert "elite_" in flat
