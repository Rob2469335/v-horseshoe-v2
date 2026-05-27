# swarm_os/apps/simulation_runner.py
"""
Simulation runner — entry point for a swarm evolution run.
Upgrade: logs diversity stats per generation so you can watch
specialists emerge in real time.
"""
from __future__ import annotations

import argparse
import logging
import random
import sys

from swarm_os.config.settings import settings
from swarm_os.kernel.environment import Environment
from swarm_os.kernel.swarm_kernel import SwarmKernel
from swarm_os.kernel.snapshot import latest_snapshot, load_snapshot
from swarm_os.scenarios.registry import build as build_scenario

log = logging.getLogger(__name__)


def _restore_from_snapshot(path) -> tuple:
    """Restore population using Genome.from_dict — safe for all snapshot versions."""
    from swarm_os.genetics import Genome
    from swarm_os.kernel.brain import registry as brain_registry
    from swarm_os.kernel.organism import Organism

    log.info("restoring population from %s", path)
    data      = load_snapshot(path)
    organisms = []

    for o_data in data["organisms"]:
        genome      = Genome.from_dict(o_data["genome"])
        brain       = brain_registry.make("swarm", genome, "general")
        org         = Organism(id=o_data["id"], brain=brain, genome=genome)
        org.fitness = o_data.get("fitness", 0.0)
        organisms.append(org)

    log.info("restored %d organisms from generation %d",
             len(organisms), data.get("generation", 0))
    return organisms, data.get("generation", 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Swarm OS evolution runner")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from latest snapshot instead of rebuilding")
    parser.add_argument("--generations", type=int, default=settings.generations,
                        help=f"Generations to run (default: {settings.generations})")
    parser.add_argument("--scenario", default=settings.scenario_name,
                        help=f"Scenario (default: {settings.scenario_name})")
    args = parser.parse_args()

    log.info("SwarmOS starting | scenario=%s generations=%d resume=%s",
             args.scenario, args.generations, args.resume)

    if settings.random_seed is not None:
        random.seed(settings.random_seed)
        log.info("random seed=%d", settings.random_seed)

    start_generation = 0

    if args.resume:
        snap = latest_snapshot()
        if snap:
            organisms, start_generation = _restore_from_snapshot(snap)
        else:
            log.warning("no snapshot found — starting fresh")
            organisms = build_scenario(args.scenario, settings.population_max)
    else:
        organisms = build_scenario(args.scenario, settings.population_max)

    log.info("population size=%d starting from generation=%d",
             len(organisms), start_generation)

    env    = Environment()
    kernel = SwarmKernel(
        organisms      = organisms,
        env            = env,
        snapshot_every = settings.snapshot_every,
        elite_count    = 2,
        fitness_decay  = 0.85,
    )
    kernel.generation = start_generation

    for gen in range(args.generations):
        try:
            summary  = kernel.step()
            top      = summary["top_organisms"]
            diversity = summary.get("diversity", {})

            if top:
                best    = top[0]
                weights = best.get("model_weights", {})
                dominant_model = max(weights, key=weights.get) if weights else "unknown"
                dominant_model_short = dominant_model.split(":")[-1]

                dom_dist = diversity.get("domain_distribution", {})
                dom_str  = " ".join(f"{k[0].upper()}:{v:.0%}" for k, v in dom_dist.items())

                log.info(
                    "gen=%03d | %-8s | pop=%d | %-22s | "
                    "fit=%6.4f avg=%6.4f | model=%-14s | div=[%s]",
                    gen + start_generation,
                    summary["task_domain"],
                    summary["population"],
                    best["id"][:22],
                    best["fitness"],
                    best["avg_fitness"],
                    dominant_model_short,
                    dom_str,
                )

        except KeyboardInterrupt:
            log.info("interrupted at generation %d — saving snapshot", kernel.generation)
            from swarm_os.kernel.snapshot import save_snapshot
            save_snapshot(kernel.organisms, kernel.generation)
            print("\nSnapshot saved. Resume with: python -m swarm_os --resume")
            sys.exit(0)

        except Exception as e:
            log.exception("generation %d failed: %s", gen, e)
            continue

    log.info("run complete | final generation=%d", kernel.generation)
    _print_final_report(kernel)


def _print_final_report(kernel: SwarmKernel) -> None:
    from swarm_os.kernel.selection import SelectionEngine
    from swarm_os.kernel.swarm_kernel import _population_diversity

    top       = SelectionEngine().top_organisms(kernel.organisms, n=5)
    diversity = _population_diversity(kernel.organisms)

    print("\n" + "═" * 68)
    print("  SWARM OS — FINAL POPULATION REPORT")
    print("═" * 68)
    print(f"  Generations completed : {kernel.generation}")
    print(f"  Final population      : {len(kernel.organisms)}")
    print(f"  Environment entropy   : {kernel.env.entropy:.4f}")
    print(f"  Fitness decay rate    : {kernel.fitness_decay}")
    print(f"  Domain distribution   : {diversity.get('domain_distribution', {})}")
    print(f"  Avg model tier        : {diversity.get('avg_model_tier', 0):.3f}")
    print(f"  Unique specialists    : {diversity.get('n_unique_dominant', 0)}")
    print()
    print("  TOP 5 ORGANISMS BY AVERAGE FITNESS:")
    print("  " + "─" * 64)

    for i, o in enumerate(top):
        cog      = o.genome.cognition
        weights  = o.genome.model_weights
        dominant = max(weights, key=weights.get)
        dom_aff  = max(["coding","research","upwork"],
                       key=lambda d: getattr(o.genome, f"{d}_affinity"))
        w_str    = ", ".join(
            f"{m.split(':')[-1]}={w:.0%}" for m, w in weights.items() if w > 0.05
        )
        print(f"\n  #{i+1}  {o.id}")
        print(f"       Role         : {dom_aff} specialist")
        print(f"       Avg fitness  : {o.genome.average_fitness:.4f}  "
              f"(total={o.fitness:.4f}, evals={o.genome.evaluations})")
        print(f"       Model dist   : {w_str}")
        print(f"       Generation   : {o.genome.generation}")
        print(f"       Active tools : {o.genome.active_tools()}")
        print(f"       Affinities   : coding={o.genome.coding_affinity:.3f}  "
              f"research={o.genome.research_affinity:.3f}  "
              f"upwork={o.genome.upwork_affinity:.3f}")
        print(f"       Cognition    : decomp={cog.decomposition_bias:.2f}  "
              f"reflect={cog.reflection_depth:.2f}  "
              f"verify={cog.verification_bias:.2f}  "
              f"halluc={cog.hallucination_sensitivity:.2f}")

    print("\n" + "═" * 68)
    print("\n  To resume:  python -m swarm_os --resume\n")

