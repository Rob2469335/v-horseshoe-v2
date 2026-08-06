"""EvolutionDaemon — runs the evolutionary kernel on REAL, persisted agent
outcomes (research-grounded: AlphaEvolve / AgentOptimizer / Reflexion successors).

Closes the loop the kernel was missing: organisms are scored by the composite
fitness that outcome_fitness.record_outcome() persists from the LIVE agent loop
(real task completion, tool success, efficiency — never LLM chat noise). Each
tick: load the population, score every genome by its best recorded outcome,
keep elites unchanged (elitism), breed via crossover+mutate, persist the next
generation. Off by default (SWARM_EVOLUTION=1); the agent loop only feeds
outcomes when the same gate is on, so there is zero overhead otherwise.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path

log = logging.getLogger(__name__)

GENOMES_PATH = Path("data/evolution/genomes.jsonl")
POPULATION_SIZE = 6
ELITE_COUNT = 2
FITNESS_DECAY = 0.85
GENERATION_TICK = 300.0  # 5 min between generations


def _load_population() -> list[dict]:
    if not GENOMES_PATH.exists():
        return []
    pop = []
    try:
        with open(GENOMES_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    pop.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return pop[-POPULATION_SIZE * 4:]  # bound


def _persist_population(pop: list[dict]) -> None:
    GENOMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GENOMES_PATH, "w", encoding="utf-8") as f:
        for g in pop[-POPULATION_SIZE:]:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")


def _seed_population() -> list[dict]:
    """Create an initial population of genome dicts (matches Genome field names
    so the kernel's crossover/mutate can consume them)."""
    pop = []
    for i in range(POPULATION_SIZE):
        pop.append({
            "id": f"genome_{i}",
            "model_tier": round(random.uniform(0.2, 0.8), 3),
            "temperature": round(random.uniform(0.3, 0.8), 3),
            "tool_genes": {
                "web_search": round(random.uniform(0.3, 0.8), 3),
                "filesystem": round(random.uniform(0.3, 0.8), 3),
                "sandbox_repl": round(random.uniform(0.2, 0.7), 3),
                "semantic_search": round(random.uniform(0.2, 0.7), 3),
            },
            "generation": 0,
            "fitness": 0.0,
        })
    return pop


def _score_genome(genome: dict) -> float:
    """Best real outcome fitness recorded for this genome. Returns a small
    prior (0.05) if none yet so novel genomes don't get zero and immediately die."""
    try:
        from swarm_os.services.outcome_fitness import best_fitness
        f = best_fitness(genome.get("id", ""))
        if f is not None:
            # Apply decayed fitness if this is an elite that has survived multiple generations.
            # (Decay is tracked in memory to prevent immortal elites from dominating forever)
            decay = FITNESS_DECAY ** genome.get("decay_generations", 0)
            return round(f * decay, 4)
        return 0.05
    except Exception:
        return 0.05


def _best_genome_tool_weights() -> dict:
    """Return the tool_genes of the highest-fitness genome in the current
    population — the EVOLVED tool-selection policy. Used by the agent loop to
    order allowed tools by what real outcomes proved most effective."""
    _, weights = get_active_genome(explore=False)
    return weights

def get_active_genome(explore: bool = True) -> tuple[str, dict]:
    """Return (genome_id, tool_weights) for the agent loop to evaluate. 
    Uses epsilon-greedy to balance exploitation (best genome) with exploration (random newborn)."""
    try:
        pop = _load_population()
        if not pop:
            return "", {}
        
        # 20% exploration of other genomes so newborns can be evaluated
        if explore and random.random() < 0.2:
            g = random.choice(pop)
            return g.get("id", ""), g.get("tool_genes", {})

        # Score by LIVE outcome fitness
        scored = []
        for g in pop:
            s = _score_genome(g)
            if s is None:
                s = 0.0
            scored.append((s, g.get("id", ""), g.get("tool_genes", {})))
        scored.sort(key=lambda x: -x[0])
        return scored[0][1], scored[0][2] if scored else {}
    except Exception:
        return "", {}


def _crossover_mutate(a: dict, b: dict, generation: int) -> dict:
    """Blend two parent genomes (per-gene uniform crossover) + small mutation."""
    child = {"id": f"genome_{int(time.time()*1000)%100000}_{random.randint(0,999)}",
             "generation": generation, "fitness": 0.0}
    # Numeric scalar genes
    for key in ("model_tier", "temperature"):
        va = a.get(key, 0.5)
        vb = b.get(key, 0.5)
        v = (va + vb) / 2.0 + random.uniform(-0.1, 0.1)
        child[key] = round(max(0.0, min(1.0, v)), 3)
    # Tool gene vectors (crossover per gene + mutation)
    ta = a.get("tool_genes", {})
    tb = b.get("tool_genes", {})
    child["tool_genes"] = {}
    for tool in set(list(ta.keys()) + list(tb.keys())):
        va = ta.get(tool, 0.5)
        vb = tb.get(tool, 0.5)
        v = random.choice([va, vb]) + random.uniform(-0.15, 0.15)
        child["tool_genes"][tool] = round(max(0.05, min(0.95, v)), 3)
    return child


def evolve_one_generation() -> dict:
    """Run one evolution generation on real persisted fitness. Returns a summary
    dict (for logging / tests). Idempotent-safe: never raises."""
    try:
        pop = _load_population()
        if not pop:
            pop = _seed_population()

        # Score every genome by its best real outcome.
        for g in pop:
            g["fitness"] = _score_genome(g)

        # Sort desc; decay fitness so old elites fade.
        pop.sort(key=lambda g: -g["fitness"])
        gen = max((g.get("generation", 0) for g in pop), default=0) + 1

        # Elitism: keep top ELITE_COUNT unchanged.
        elites = pop[:ELITE_COUNT]
        # Decay elite fitness (survivor cost) by incrementing decay_generations.
        for g in elites:
            g["decay_generations"] = g.get("decay_generations", 0) + 1
            g["fitness"] = _score_genome(g)

        # Breed the rest from the top half (tournament-lite).
        parents = pop[: max(ELITE_COUNT, POPULATION_SIZE // 2)]
        children = []
        while len(elites) + len(children) < POPULATION_SIZE:
            a = random.choice(parents)
            b = random.choice(parents)
            children.append(_crossover_mutate(a, b, gen))

        new_pop = (elites + children)[:POPULATION_SIZE]
        _persist_population(new_pop)

        best = max((g.get("fitness", 0.0) for g in new_pop), default=0.0)
        return {"generation": gen, "population": len(new_pop),
                "best_fitness": round(best, 4), "elites": [e.get("id") for e in elites]}
    except Exception as exc:
        log.warning("evolution generation failed: %s", exc)
        return {"generation": -1, "population": 0, "best_fitness": 0.0, "error": str(exc)}


async def evolution_daemon(interval_seconds: float = GENERATION_TICK, first_delay: float = 0.0):
    """Background daemon tick: run one generation each interval. Called from
    main.py when SWARM_EVOLUTION=1. The agent loop feeds real outcomes into
    outcome_fitness (same gate), so this selects on grounded fitness.

    `first_delay` defers the FIRST tick so a heavy generation (disk reads,
    crossover) does not run during the backend startup window and stall the
    API from becoming responsive."""
    if first_delay > 0:
        await asyncio.sleep(first_delay)
    while True:
        try:
            summary = await asyncio.to_thread(evolve_one_generation)
            log.info("[evolution] generation=%s pop=%s best_fitness=%s",
                     summary.get("generation"), summary.get("population"),
                     summary.get("best_fitness"))
        except Exception as exc:
            log.warning("[evolution] daemon tick failed: %s", exc)
        await asyncio.sleep(interval_seconds)
