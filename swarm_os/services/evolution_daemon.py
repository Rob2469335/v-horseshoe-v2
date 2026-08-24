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
STAGED_DIR = Path("data/evolution/staged")
POPULATION_SIZE = 6
ELITE_COUNT = 2
FITNESS_DECAY = 0.85
GENERATION_TICK = 300.0  # 5 min between generations


def _load_population(path: Path | None = None) -> list[dict]:
    """Load a population from a genomes file (active by default, or a staged file).

    NOTE (2026-08-07, real bug fixed): the autonomy policy said
    `evolution.promotion = staged_human_approved` — new generations must be
    STAGED and approved before they become active — but the code wrote new
    generations STRAIGHT to the active GENOMES_PATH every tick with no staging
    and no gate. The policy file described intent the code never implemented:
    the daemon had been unconditionally auto-promoting this whole time. Fixed
    here: evolve_one_generation now writes to STAGED_DIR/<gen>.jsonl and leaves
    the active population untouched until a human approves via promote_staged.
    """
    if not path.exists():
        return []
    pop = []
    try:
        with open(path, "r", encoding="utf-8") as f:
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
    return pop[-POPULATION_SIZE * 4 :]  # bound


def _persist_population(pop: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for g in pop[-POPULATION_SIZE:]:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")


def list_staged_generations() -> list[dict]:
    """Return staged generations (newest first) for inspection/approval. Each has
    {gen, path, best_fitness, elites, population}."""
    if not STAGED_DIR.exists():
        return []
    out = []
    for p in sorted(STAGED_DIR.glob("gen_*.jsonl"), key=lambda p: p.name):
        try:
            pop = _load_population(p)
            best = max((g.get("fitness", 0.0) for g in pop), default=0.0)
            out.append(
                {
                    "gen": p.stem.replace("gen_", ""),
                    "path": str(p),
                    "best_fitness": round(best, 4),
                    "elites": [g.get("id") for g in pop[:ELITE_COUNT]],
                    "population": len(pop),
                }
            )
        except Exception:
            continue
    out.reverse()  # newest first
    return out


_TOOL_FLOORS = {
    "filesystem": 0.50,
    "web_search": 0.40,
    "web_fetch": 0.40,
}


def validate_tool_floors(genomes: list[dict]) -> tuple[bool, str]:
    """Verify that all genomes in a population satisfy the minimum tool genes floors."""
    for g in genomes:
        tool_genes = g.get("tool_genes") or {}
        for tool, floor in _TOOL_FLOORS.items():
            val = tool_genes.get(tool, 0.0)
            if val < floor:
                return (
                    False,
                    f"Genome {g.get('id')} tool_gene '{tool}' ({val}) below floor ({floor})",
                )
    return True, "All genomes satisfy tool floors"


def rollback_promotion() -> dict:
    """Callable recovery primitive: restore active genomes from genomes.jsonl.bak."""
    bak = GENOMES_PATH.with_suffix(".jsonl.bak")
    if not bak.exists():
        return {
            "ok": False,
            "reason": "No backup snapshot (genomes.jsonl.bak) found to rollback",
        }
    try:
        import os
        import shutil

        tmp = GENOMES_PATH.with_suffix(".jsonl.tmp")
        shutil.copyfile(bak, tmp)
        os.replace(tmp, GENOMES_PATH)
        return {"ok": True, "action": "rollback_promotion"}
    except Exception as e:
        return {"ok": False, "reason": f"Rollback failed: {e}"}


def promote_staged_generation(gen: str | int, enforce_floors: bool = True) -> dict:
    """Approve a staged generation: atomically replace the ACTIVE population
    with the staged one. Creates an atomic backup (genomes.jsonl.bak).
    Returns a summary."""
    staged_path = STAGED_DIR / f"gen_{gen}.jsonl"
    if not staged_path.exists():
        return {"ok": False, "reason": f"staged generation {gen} not found"}
    try:
        staged = _load_population(staged_path)
        if not staged:
            return {"ok": False, "reason": f"staged generation {gen} is empty"}

        if enforce_floors:
            ok, reason = validate_tool_floors(staged)
            if not ok:
                return {"ok": False, "reason": f"tool floor check failed: {reason}"}

        import os
        import shutil

        GENOMES_PATH.parent.mkdir(parents=True, exist_ok=True)
        if GENOMES_PATH.exists():
            bak = GENOMES_PATH.with_suffix(".jsonl.bak")
            shutil.copyfile(GENOMES_PATH, bak)

        tmp = GENOMES_PATH.with_suffix(".jsonl.tmp")
        _persist_population(staged, tmp)
        os.replace(tmp, GENOMES_PATH)
        return {
            "ok": True,
            "gen": str(gen),
            "population": len(staged),
            "best_fitness": round(
                max((g.get("fitness", 0.0) for g in staged), default=0.0), 4
            ),
        }
    except Exception as exc:
        return {"ok": False, "reason": f"promotion failed: {exc}"}


def _seed_population() -> list[dict]:
    """Create an initial population of genome dicts (matches Genome field names
    so the kernel's crossover/mutate can consume them)."""
    pop = []
    for i in range(POPULATION_SIZE):
        pop.append(
            {
                "id": f"genome_{i}",
                "model_tier": round(random.uniform(0.2, 0.8), 3),
                "temperature": round(random.uniform(0.3, 0.8), 3),
                "tool_genes": {
                    "filesystem": round(random.uniform(0.5, 0.8), 3),
                    "web_search": round(random.uniform(0.4, 0.8), 3),
                    "web_fetch": round(random.uniform(0.4, 0.8), 3),
                    "sandbox_repl": round(random.uniform(0.2, 0.7), 3),
                    "semantic_search": round(random.uniform(0.2, 0.7), 3),
                },
                "generation": 0,
                "fitness": 0.0,
            }
        )
    return pop


def _score_genome(genome: dict) -> float:
    """Best real outcome fitness recorded for this genome. Falls back to the
    best AGGREGATE outcome across all agents when no exact `genome_<n>` record
    exists: the population is a single shared tool-policy lineage consumed by
    every agent (one `_best_genome_tool_weights()` result reordered into each
    allowed-tool list), and live outcomes are keyed `agent:<id>` in
    fitness.jsonl, so without the aggregate fallback every genome scores the
    flat 0.05 prior and the population freezes (0.0425 elite plateau). 0.05 is
    returned only when there is no real signal at all."""
    try:
        from swarm_os.services.outcome_fitness import (
            best_fitness,
            best_aggregate_fitness,
        )

        exact = best_fitness(genome.get("id", ""))
        if exact is not None:
            decay = FITNESS_DECAY ** genome.get("decay_generations", 0)
            return round(exact * decay, 4)

        agg = best_aggregate_fitness()
        if agg is not None:
            return round(agg * 0.80, 4)
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
        pop = _load_population(GENOMES_PATH)
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
    child = {
        "id": f"genome_{int(time.time() * 1000) % 100000}_{random.randint(0, 999)}",
        "generation": generation,
        "fitness": 0.0,
    }
    # Numeric scalar genes
    for key in ("model_tier", "temperature"):
        va = a.get(key, 0.5)
        vb = b.get(key, 0.5)
        v = (va + vb) / 2.0 + random.uniform(-0.1, 0.1)
        child[key] = round(max(0.0, min(1.0, v)), 3)
    # Tool gene vectors (crossover per gene + mutation with essential tool floors)
    ta = a.get("tool_genes", {})
    tb = b.get("tool_genes", {})
    child["tool_genes"] = {}
    all_tools = set(list(ta.keys()) + list(tb.keys()) + list(_TOOL_FLOORS.keys()))
    for tool in all_tools:
        default_val = _TOOL_FLOORS.get(tool, 0.5)
        va = ta.get(tool, default_val)
        vb = tb.get(tool, default_val)
        v = random.choice([va, vb]) + random.uniform(-0.15, 0.15)
        floor = _TOOL_FLOORS.get(tool, 0.05)
        child["tool_genes"][tool] = round(max(floor, min(0.95, v)), 3)
    return child


def evolve_one_generation(
    auto_promote: bool | None = None, min_improvement: float = 0.03
) -> dict:
    """Run one evolution generation on real persisted fitness. Returns a summary
    dict (for logging / tests). Idempotent-safe: never raises.

    When `auto_promote` is True (or env SWARM_EVOLUTION_AUTO_PROMOTE=1), the
    generation is automatically evaluated against the active population and promoted
    if `staged_best >= active_best + min_improvement` and tool floors are met."""
    try:
        import os

        pop = _load_population(GENOMES_PATH)
        if not pop:
            pop = _seed_population()

        # Score every genome by its best real outcome.
        for g in pop:
            g["fitness"] = _score_genome(g)

        # Sort desc; decay fitness so old elites fade.
        pop.sort(key=lambda g: -g["fitness"])
        active_best = max((g.get("fitness", 0.0) for g in pop), default=0.0)

        # EVO-1: derive the next generation number from what is ALREADY STAGED,
        # not the active population. The active pop stays at its last APPROVED
        # generation while a staged one awaits human approval — deriving from
        # the active pop would re-stage the same gen number and clobber the
        # staged-but-unapproved generation. Malformed staged filenames are
        # ignored (never raise, never delete).
        staged_gens: list[int] = []
        for p in STAGED_DIR.glob("gen_*.jsonl"):
            try:
                staged_gens.append(int(p.stem.split("_")[1]))
            except (IndexError, ValueError):
                log.warning(f"Malformed staged file ignored in gen calc: {p}")
        max_active_gen = max((g.get("generation", 0) for g in pop), default=0)
        gen = max(max(staged_gens, default=0), max_active_gen) + 1

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

        for c in children:
            c["fitness"] = _score_genome(c)

        new_pop = (elites + children)[:POPULATION_SIZE]
        new_pop.sort(key=lambda g: -g.get("fitness", 0.0))
        # Write the new generation to the STAGED dir
        _staged_path = STAGED_DIR / f"gen_{gen}.jsonl"
        _persist_population(new_pop, _staged_path)

        staged_best = max((g.get("fitness", 0.0) for g in new_pop), default=0.0)

        should_auto_promote = (
            auto_promote
            if auto_promote is not None
            else os.getenv("SWARM_EVOLUTION_AUTO_PROMOTE", "").lower()
            in ("1", "true")
        )

        promoted = False
        promotion_reason = ""
        if should_auto_promote:
            if staged_best >= (active_best + min_improvement) and staged_best > 0.0:
                prom_res = promote_staged_generation(gen, enforce_floors=True)
                promoted = prom_res.get("ok", False)
                promotion_reason = (
                    "Promoted to active population"
                    if promoted
                    else prom_res.get("reason", "Promotion failed")
                )
            else:
                promotion_reason = (
                    f"Staged best ({staged_best:.4f}) did not beat active best "
                    f"({active_best:.4f}) by required margin ({min_improvement:.4f})"
                )

        return {
            "generation": gen,
            "population": len(new_pop),
            "active_best": round(active_best, 4),
            "staged_best": round(staged_best, 4),
            "best_fitness": round(staged_best, 4),
            "elites": [e.get("id") for e in elites],
            "promoted": promoted,
            "promotion_reason": promotion_reason,
            "staged": not promoted,
            "staged_path": str(_staged_path),
        }
    except Exception as exc:
        log.warning("evolution generation failed: %s", exc)
        return {
            "generation": -1,
            "population": 0,
            "best_fitness": 0.0,
            "error": str(exc),
        }


async def evolution_daemon(
    interval_seconds: float = GENERATION_TICK, first_delay: float = 0.0
):
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
            log.info(
                "[evolution] generation=%s pop=%s best_fitness=%s",
                summary.get("generation"),
                summary.get("population"),
                summary.get("best_fitness"),
            )
        except Exception as exc:
            log.warning("[evolution] daemon tick failed: %s", exc)
        await asyncio.sleep(interval_seconds)
