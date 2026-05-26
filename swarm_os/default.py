# swarm_os/scenarios/default.py
"""
Default scenario — 6 diverse seed organisms.
normalize_affinities() called after each Genome() so domain competition is valid.
"""
from __future__ import annotations
from typing import List

from swarm_os.kernel.brain import registry as brain_registry
from swarm_os.kernel.genetics import CognitivePolicy, Genome, MCP_TOOL_REGISTRY, normalize_affinities
from swarm_os.kernel.organism import Organism


def _make(org_id: str, genome: Genome) -> Organism:
    normalize_affinities(genome)   # ensure sum=1.0 before brain is built
    brain = brain_registry.make("swarm", genome, task_domain="general")
    return Organism(id=org_id, brain=brain, genome=genome)


def build(population_max: int = 6) -> List[Organism]:
    seeds = [

        # 0: Deep coder — low temp, high verification, filesystem + code_exec
        _make("seed_0_coder", Genome(
            model_tier=0.5, temperature=0.2, reasoning_depth=0.8, verbosity=0.4,
            coding_affinity=0.85, research_affinity=0.2, upwork_affinity=0.15,
            context_budget=0.6, retrieval_top_k=0.4, memory_window=0.5, crossover_stability=0.6,
            cognition=CognitivePolicy(
                decomposition_bias=0.7, max_subtasks=0.5, reflection_depth=0.6,
                self_critique_bias=0.7, verification_bias=0.8, hallucination_sensitivity=0.9,
                parallel_tool_calls=0.3, retry_aggression=0.6, fallback_model_bias=0.4,
                escalation_bias=0.3, summarization_bias=0.3, compression_ratio=0.6,
                memory_read_bias=0.5, memory_write_bias=0.6,
            ),
            tool_genes={"web_search":0.2,"playwright":0.1,"filesystem":0.9,
                        "context7":0.7,"qdrant_recall":0.6,"code_exec":0.9},
        )),

        # 1: Web researcher — high search, verbose, reflective
        _make("seed_1_researcher", Genome(
            model_tier=0.55, temperature=0.6, reasoning_depth=0.75, verbosity=0.75,
            coding_affinity=0.2, research_affinity=0.85, upwork_affinity=0.25,
            context_budget=0.8, retrieval_top_k=0.7, memory_window=0.7, crossover_stability=0.5,
            cognition=CognitivePolicy(
                decomposition_bias=0.6, max_subtasks=0.6, reflection_depth=0.8,
                self_critique_bias=0.6, verification_bias=0.75, hallucination_sensitivity=0.8,
                parallel_tool_calls=0.6, retry_aggression=0.7, fallback_model_bias=0.5,
                escalation_bias=0.4, summarization_bias=0.8, compression_ratio=0.4,
                memory_read_bias=0.8, memory_write_bias=0.7,
            ),
            tool_genes={"web_search":0.9,"playwright":0.4,"filesystem":0.2,
                        "context7":0.85,"qdrant_recall":0.75,"code_exec":0.2},
        )),

        # 2: Upwork scanner — terse, fast, playwright
        _make("seed_2_upwork", Genome(
            model_tier=0.4, temperature=0.25, reasoning_depth=0.35, verbosity=0.25,
            coding_affinity=0.15, research_affinity=0.3, upwork_affinity=0.9,
            context_budget=0.35, retrieval_top_k=0.3, memory_window=0.3, crossover_stability=0.55,
            cognition=CognitivePolicy(
                decomposition_bias=0.25, max_subtasks=0.3, reflection_depth=0.3,
                self_critique_bias=0.4, verification_bias=0.5, hallucination_sensitivity=0.6,
                parallel_tool_calls=0.7, retry_aggression=0.8, fallback_model_bias=0.6,
                escalation_bias=0.5, summarization_bias=0.7, compression_ratio=0.8,
                memory_read_bias=0.4, memory_write_bias=0.5,
            ),
            tool_genes={"web_search":0.7,"playwright":0.9,"filesystem":0.1,
                        "context7":0.2,"qdrant_recall":0.4,"code_exec":0.1},
        )),

        # 3: Generalist — medium everything
        _make("seed_3_generalist", Genome(
            model_tier=0.5, temperature=0.5, reasoning_depth=0.5, verbosity=0.5,
            coding_affinity=0.4, research_affinity=0.4, upwork_affinity=0.4,
            context_budget=0.5, retrieval_top_k=0.5, memory_window=0.5, crossover_stability=0.5,
            cognition=CognitivePolicy(
                decomposition_bias=0.5, max_subtasks=0.5, reflection_depth=0.5,
                self_critique_bias=0.5, verification_bias=0.5, hallucination_sensitivity=0.5,
                parallel_tool_calls=0.5, retry_aggression=0.5, fallback_model_bias=0.5,
                escalation_bias=0.5, summarization_bias=0.5, compression_ratio=0.5,
                memory_read_bias=0.5, memory_write_bias=0.5,
            ),
            tool_genes={t: 0.5 for t in MCP_TOOL_REGISTRY},
        )),

        # 4: Heavy thinker — 14b, deep decomposition, slow but thorough
        _make("seed_4_heavy", Genome(
            model_tier=0.85, temperature=0.4, reasoning_depth=0.9, verbosity=0.8,
            coding_affinity=0.55, research_affinity=0.65, upwork_affinity=0.35,
            context_budget=0.9, retrieval_top_k=0.8, memory_window=0.8, crossover_stability=0.65,
            cognition=CognitivePolicy(
                decomposition_bias=0.85, max_subtasks=0.75, reflection_depth=0.9,
                self_critique_bias=0.85, verification_bias=0.85, hallucination_sensitivity=0.9,
                parallel_tool_calls=0.4, retry_aggression=0.5, fallback_model_bias=0.3,
                escalation_bias=0.2, summarization_bias=0.6, compression_ratio=0.3,
                memory_read_bias=0.8, memory_write_bias=0.7,
            ),
            tool_genes={"web_search":0.6,"playwright":0.3,"filesystem":0.6,
                        "context7":0.7,"qdrant_recall":0.85,"code_exec":0.5},
        )),

        # 5: Fast triage — 3b, minimal tools, wins on speed, loses on quality
        _make("seed_5_triage", Genome(
            model_tier=0.1, temperature=0.3, reasoning_depth=0.2, verbosity=0.2,
            coding_affinity=0.3, research_affinity=0.25, upwork_affinity=0.5,
            context_budget=0.2, retrieval_top_k=0.2, memory_window=0.2, crossover_stability=0.4,
            cognition=CognitivePolicy(
                decomposition_bias=0.2, max_subtasks=0.2, reflection_depth=0.2,
                self_critique_bias=0.2, verification_bias=0.3, hallucination_sensitivity=0.4,
                parallel_tool_calls=0.3, retry_aggression=0.3, fallback_model_bias=0.7,
                escalation_bias=0.7, summarization_bias=0.4, compression_ratio=0.9,
                memory_read_bias=0.3, memory_write_bias=0.3,
            ),
            tool_genes={"web_search":0.6,"playwright":0.2,"filesystem":0.2,
                        "context7":0.2,"qdrant_recall":0.4,"code_exec":0.2},
        )),
    ]

    return seeds[:population_max]
