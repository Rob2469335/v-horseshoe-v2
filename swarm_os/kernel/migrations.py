from __future__ import annotations

from copy import deepcopy

def migrate_snapshot(data: dict) -> dict:
    data = deepcopy(data)
    version = int(data.get("snapshot_version", 1))

    if version < 3:
        for org in data.get("organisms", []):
            g = org.get("genome", {})
            if "genes" in g or "architecture" in g:
                org["genome"] = {
                    "model_tier": g.get("model_tier", 0.5),
                    "temperature": g.get("temperature", 0.5),
                    "reasoning_depth": g.get("reasoning_depth", 0.5),
                    "verbosity": g.get("verbosity", 0.5),
                    "coding_affinity": g.get("coding_affinity", 0.33),
                    "research_affinity": g.get("research_affinity", 0.33),
                    "upwork_affinity": g.get("upwork_affinity", 0.33),
                    "context_budget": g.get("context_budget", 0.5),
                    "retrieval_top_k": g.get("retrieval_top_k", 0.5),
                    "memory_window": g.get("memory_window", 0.5),
                    "mutation_rate": g.get("mutation_rate", 0.1),
                    "crossover_stability": g.get("crossover_stability", 0.5),
                    "parent_id": g.get("parent_id"),
                    "generation": g.get("generation", 0),
                    "cognition": g.get("cognition", {}),
                    "tool_genes": g.get("tool_genes", {}),
                    "lifetime_fitness": g.get("lifetime_fitness", 0.0),
                    "evaluations": g.get("evaluations", 0),
                }
        data["snapshot_version"] = 3

    return data
