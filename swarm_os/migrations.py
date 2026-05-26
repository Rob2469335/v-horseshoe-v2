# swarm_os/kernel/migrations.py
"""
Snapshot migration system.
Version history:
  v1 — original (genes as flat dict, architecture as list)
  v2 — fitness added per organism
  v3 — Genome dataclass with named fields
  v4 — CognitivePolicy, tool_genes dict, context_budget,
        retrieval_top_k, memory_window, crossover_stability,
        lifetime_fitness, evaluations  (current)
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)

CURRENT_VERSION = 4

# ── Default values for backfilling missing fields ──────────────────────────────
_GENOME_DEFAULTS = {
    "model_tier":           0.5,
    "temperature":          0.5,
    "reasoning_depth":      0.5,
    "verbosity":            0.5,
    "coding_affinity":      0.33,
    "research_affinity":    0.33,
    "upwork_affinity":      0.33,
    "context_budget":       0.5,
    "retrieval_top_k":      0.5,
    "memory_window":        0.5,
    "mutation_rate":        0.1,
    "crossover_stability":  0.5,
    "parent_id":            None,
    "generation":           0,
    "lifetime_fitness":     0.0,
    "evaluations":          0,
}

_COGNITION_DEFAULTS = {
    "decomposition_bias":       0.5,
    "max_subtasks":             0.5,
    "reflection_depth":         0.5,
    "self_critique_bias":       0.5,
    "verification_bias":        0.5,
    "hallucination_sensitivity":0.5,
    "parallel_tool_calls":      0.5,
    "retry_aggression":         0.5,
    "fallback_model_bias":      0.5,
    "escalation_bias":          0.5,
    "summarization_bias":       0.5,
    "compression_ratio":        0.5,
    "memory_read_bias":         0.5,
    "memory_write_bias":        0.5,
}

_DEFAULT_TOOL_GENES = {
    "web_search":    0.5,
    "playwright":    0.5,
    "filesystem":    0.5,
    "context7":      0.5,
    "qdrant_recall": 0.5,
    "code_exec":     0.5,
}

_MCP_REGISTRY = list(_DEFAULT_TOOL_GENES.keys())


def _v1_to_v2(data: dict) -> dict:
    for o in data.get("organisms", []):
        o.setdefault("fitness", 0.0)
    data["snapshot_version"] = 2
    log.info("migrated snapshot v1 → v2")
    return data


def _v2_to_v3(data: dict) -> dict:
    """Convert old genes dict + architecture list to named Genome fields."""
    for o in data.get("organisms", []):
        old = o.get("genome", {})
        old_genes = old.get("genes", {})

        new = dict(_GENOME_DEFAULTS)

        # Best-effort mapping from old flat genes
        if "temperature" in old_genes:
            new["temperature"] = float(old_genes["temperature"])
        if "cost" in old_genes:
            new["tool_bias_hint"] = float(old_genes.get("cost", 0.5))

        # Preserve generation lineage
        new["generation"]    = old.get("generation", 0)
        new["parent_id"]     = old.get("parent_id", None)
        new["mutation_rate"] = old.get("mutation_rate", 0.1)

        # Preserve valid architecture entries as tool_genes hints
        old_arch = old.get("architecture", [])
        valid = [t for t in old_arch if t in _MCP_REGISTRY]
        # Will be formalised in v3→v4

        o["genome"] = {**new, "_old_arch": valid}

    data["snapshot_version"] = 3
    log.info("migrated snapshot v2 → v3")
    return data


def _v3_to_v4(data: dict) -> dict:
    """
    Add CognitivePolicy, tool_genes dict, and new scalar genome fields.
    All new fields default to 0.5 (neutral) so old organisms
    are viable but unspecialised — evolution will shape them.
    """
    for o in data.get("organisms", []):
        g = o.get("genome", {})

        # ── New scalar fields ────────────────────────────────────────────────
        for k, v in _GENOME_DEFAULTS.items():
            g.setdefault(k, v)

        # ── tool_genes dict ───────────────────────────────────────────────────
        if "tool_genes" not in g:
            tool_genes = dict(_DEFAULT_TOOL_GENES)
            # If old architecture list survived, boost those tools slightly
            old_arch = g.pop("_old_arch", [])
            for t in old_arch:
                if t in tool_genes:
                    tool_genes[t] = 0.75
            g["tool_genes"] = tool_genes
        else:
            # Ensure all tools present
            for t, default in _DEFAULT_TOOL_GENES.items():
                g["tool_genes"].setdefault(t, default)

        # ── CognitivePolicy ───────────────────────────────────────────────────
        if "cognition" not in g:
            g["cognition"] = dict(_COGNITION_DEFAULTS)
        else:
            for k, v in _COGNITION_DEFAULTS.items():
                g["cognition"].setdefault(k, v)

        # Clean up stale keys that no longer exist in Genome
        for stale in ("genes", "architecture", "tool_bias", "memory_bias",
                      "search_bias", "tool_bias_hint"):
            g.pop(stale, None)

        # Normalize affinities so migrated genomes have valid domain competition
        total = (g.get("coding_affinity", 0.33) +
                 g.get("research_affinity", 0.33) +
                 g.get("upwork_affinity", 0.33))
        if total > 1e-9:
            g["coding_affinity"]   /= total
            g["research_affinity"] /= total
            g["upwork_affinity"]   /= total

        o["genome"] = g

    data["snapshot_version"] = 4
    log.info("migrated snapshot v3 → v4")
    return data


_MIGRATIONS = {
    1: _v1_to_v2,
    2: _v2_to_v3,
    3: _v3_to_v4,
}


def migrate_snapshot(data: dict) -> dict:
    """Apply all needed migrations to bring snapshot up to current version."""
    version = data.get("snapshot_version", 1)
    while version < CURRENT_VERSION:
        if version not in _MIGRATIONS:
            raise ValueError(
                f"No migration path from snapshot version {version}. "
                f"Current: {CURRENT_VERSION}"
            )
        data = _MIGRATIONS[version](data)
        version = data["snapshot_version"]
    return data
