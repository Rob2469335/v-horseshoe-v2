# swarm_os/kernel/genetics.py
"""
Genome design for Swarm OS organisms.
Genes control how each organism calls Horseshoe Swarm (port 11436).

CognitivePolicy — second genome layer controlling how the organism thinks.
tool_genes      — continuous probability weights per MCP tool (sigmoid activation).
sample_model()  — probabilistic model selection, not hard tier cutoffs.
normalize_affinities() — keeps domain affinities competitive (sum to 1.0).
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# ── MCP tool registry ─────────────────────────────────────────────────────────
MCP_TOOL_REGISTRY: List[str] = [
    "web_search",       # Swarm search: Brave / Tavily / SearXNG
    "playwright",       # Browser automation via MCP port 8931
    "filesystem",       # VS Code file read/write via Filesystem MCP
    "context7",         # Library docs lookup via Context7 MCP
    "qdrant_recall",    # Long-term memory retrieval via Qdrant
    "code_exec",        # Code block extraction and validation
]

# ── Model tier mapping ────────────────────────────────────────────────────────
MODEL_TIERS: Dict[str, str] = {
    "triage": "qwen2.5:3b-instruct",
    "fast":   "qwen2.5-coder:7b-16k",
    "heavy":  "qwen3:14b",
}


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def normalize_affinities(genome: "Genome") -> None:
    """
    Normalize domain affinities so they sum to 1.0.
    Called after every mutate() and crossover() so gaining affinity
    in one domain costs affinity in others — forcing real specialization.
    Without this, all affinities drift upward and selection pressure
    produces no specialists.
    """
    total = genome.coding_affinity + genome.research_affinity + genome.upwork_affinity
    if total > 1e-9:
        genome.coding_affinity   /= total
        genome.research_affinity /= total
        genome.upwork_affinity   /= total


def model_distribution(tier: float) -> Dict[str, float]:
    """Soft probability distribution over models — not hard cutoffs."""
    triage_weight = clamp(1.0 - (tier * 2.0))
    heavy_weight  = clamp((tier * 2.0) - 1.0)
    fast_weight   = clamp(1.0 - abs((tier - 0.5) * 2.0))
    total = triage_weight + fast_weight + heavy_weight + 1e-9
    return {
        MODEL_TIERS["triage"]: triage_weight / total,
        MODEL_TIERS["fast"]:   fast_weight   / total,
        MODEL_TIERS["heavy"]:  heavy_weight  / total,
    }


def sample_model(tier: float) -> str:
    """Sample a model from the probability distribution for this tier value."""
    distribution = model_distribution(tier)
    cumulative   = 0.0
    roll         = random.random()
    for model, weight in distribution.items():
        cumulative += weight
        if roll <= cumulative:
            return model
    return MODEL_TIERS["fast"]


# ── CognitivePolicy ───────────────────────────────────────────────────────────

@dataclass
class CognitivePolicy:
    """
    Second genome layer — controls HOW the organism thinks.
    Each field shapes the system prompt sent to Swarm.
    All values 0.0–1.0.
    """
    decomposition_bias:        float = 0.5  # break task into subtasks
    max_subtasks:              float = 0.5  # how many subtasks (maps to 2–6)
    reflection_depth:          float = 0.5  # reflect before finalizing
    self_critique_bias:        float = 0.5  # critique own answer
    verification_bias:         float = 0.5  # verify facts before including
    hallucination_sensitivity: float = 0.5  # refuse to invent facts
    parallel_tool_calls:       float = 0.5  # call tools together vs sequentially
    retry_aggression:          float = 0.5  # retry on tool failure
    fallback_model_bias:       float = 0.5  # escalate to heavier model on failure
    escalation_bias:           float = 0.5  # escalate hard problems
    summarization_bias:        float = 0.5  # end with summary
    compression_ratio:         float = 0.5  # compress context aggressively
    memory_read_bias:          float = 0.5  # read from Qdrant memory
    memory_write_bias:         float = 0.5  # write results to Qdrant memory

    def mutate(self, delta: float) -> None:
        for f in self.__dataclass_fields__:
            setattr(self, f, clamp(getattr(self, f) + random.uniform(-delta, delta)))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CognitivePolicy":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ── Genome ────────────────────────────────────────────────────────────────────

@dataclass
class Genome:
    # Model selection
    model_tier:           float = 0.5    # 0=triage 3b, 0.5=fast 7b, 1=heavy 14b

    # Reasoning style
    temperature:          float = 0.5    # 0=deterministic, 1=creative
    reasoning_depth:      float = 0.5    # 0=direct, 1=step-by-step
    verbosity:            float = 0.5    # 0=terse, 1=detailed

    # Task domain affinities — always normalized to sum to 1.0
    coding_affinity:      float = 0.33
    research_affinity:    float = 0.33
    upwork_affinity:      float = 0.33

    # Memory and context
    context_budget:       float = 0.5    # maps to 512–4096 token budget
    retrieval_top_k:      float = 0.5    # maps to 3–20 Qdrant results
    memory_window:        float = 0.5    # how far back to recall

    # Evolutionary parameters
    mutation_rate:        float = 0.1
    crossover_stability:  float = 0.5    # 0=pick-one, 1=average on crossover
    parent_id:            Optional[str] = None
    generation:           int = 0

    # Cognitive layer
    cognition: CognitivePolicy = field(default_factory=CognitivePolicy)

    # MCP tool probability weights (sigmoid activation)
    tool_genes: Dict[str, float] = field(
        default_factory=lambda: {t: random.uniform(0.25, 0.75) for t in MCP_TOOL_REGISTRY}
    )

    # Fitness tracking (for adaptive mutation)
    lifetime_fitness: float = 0.0
    evaluations:      int   = 0

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def model(self) -> str:
        """Sample model from probability distribution — not a hard cutoff."""
        return sample_model(self.model_tier)

    @property
    def model_weights(self) -> Dict[str, float]:
        """
        Full probability distribution over models.
        Used by swarm_kernel summaries and dashboard to show
        'this organism is 60% 7b, 30% 14b, 10% 3b' rather than
        just whichever model happened to roll this generation.
        """
        return model_distribution(self.model_tier)

    @property
    def actual_temperature(self) -> float:
        return round(self.temperature * 1.2, 2)

    @property
    def average_fitness(self) -> float:
        return 0.0 if self.evaluations == 0 else self.lifetime_fitness / self.evaluations

    # ── Methods ───────────────────────────────────────────────────────────────

    def active_tools(self, seed: Optional[int] = None) -> List[str]:
        """
        Return tools whose sigmoid-activated weight passes a random threshold.
        High tool_gene → almost always active.
        Low tool_gene  → almost never active.

        seed: optional int for deterministic output (used by kernel for
              consistent logging — same generation always shows same tools).
        """
        rng = random.Random(seed)
        return [
            tool for tool, weight in self.tool_genes.items()
            if rng.random() < sigmoid((weight - 0.5) * 6)
        ]

    def record_fitness(self, value: float) -> None:
        self.lifetime_fitness += value
        self.evaluations      += 1

    def copy(self, new_parent_id: str) -> "Genome":
        """
        Explicit field-by-field copy — faster and safer than deepcopy.
        Child starts with zero fitness history (clean slate for adaptive mutation).
        """
        child = Genome(
            **{
                f: getattr(self, f)
                for f in self.__dataclass_fields__
                if f not in ("cognition", "tool_genes", "parent_id",
                             "lifetime_fitness", "evaluations", "generation")
            }
        )
        child.cognition        = CognitivePolicy.from_dict(self.cognition.to_dict())
        child.tool_genes       = dict(self.tool_genes)
        child.parent_id        = new_parent_id
        child.generation       = self.generation + 1
        child.lifetime_fitness = 0.0
        child.evaluations      = 0
        return child

    def to_dict(self) -> dict:
        return {
            "model_tier":          self.model_tier,
            "model":               self.model,
            "model_weights":       self.model_weights,   # for dashboard/logging
            "temperature":         self.temperature,
            "reasoning_depth":     self.reasoning_depth,
            "verbosity":           self.verbosity,
            "coding_affinity":     self.coding_affinity,
            "research_affinity":   self.research_affinity,
            "upwork_affinity":     self.upwork_affinity,
            "context_budget":      self.context_budget,
            "retrieval_top_k":     self.retrieval_top_k,
            "memory_window":       self.memory_window,
            "mutation_rate":       self.mutation_rate,
            "crossover_stability": self.crossover_stability,
            "parent_id":           self.parent_id,
            "generation":          self.generation,
            "tool_genes":          dict(self.tool_genes),
            "cognition":           self.cognition.to_dict(),
            "average_fitness":     self.average_fitness,
            "lifetime_fitness":    self.lifetime_fitness,
            "evaluations":         self.evaluations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Genome":
        payload  = dict(data)
        cog_data = payload.pop("cognition", {})
        # Remove computed/non-field keys before passing to __init__
        for k in ("model", "model_weights", "average_fitness"):
            payload.pop(k, None)
        genome           = cls(**{k: v for k, v in payload.items()
                                  if k in cls.__dataclass_fields__})
        genome.cognition = CognitivePolicy.from_dict(cog_data)
        return genome


# ── Mutation ──────────────────────────────────────────────────────────────────

def mutate(genome: Genome) -> None:
    """
    Adaptive mutation — poorly-performing organisms mutate more aggressively.
    Calls normalize_affinities() after drift so domain competition stays real.
    """
    fitness_modifier = 1.0 - clamp(genome.average_fitness)
    adaptive_delta   = clamp(genome.mutation_rate * (0.5 + fitness_modifier), 0.01, 0.5)

    for fname in [
        "model_tier", "temperature", "reasoning_depth", "verbosity",
        "coding_affinity", "research_affinity", "upwork_affinity",
        "context_budget", "retrieval_top_k", "memory_window",
        "crossover_stability",
    ]:
        setattr(genome, fname,
                clamp(getattr(genome, fname) + random.uniform(-adaptive_delta, adaptive_delta)))

    # Keep affinities competitive after drift
    normalize_affinities(genome)

    genome.cognition.mutate(adaptive_delta)

    for tool in genome.tool_genes:
        if random.random() < genome.mutation_rate:
            genome.tool_genes[tool] = clamp(
                genome.tool_genes[tool] + random.uniform(-adaptive_delta, adaptive_delta)
            )

    # Occasional random reset of one tool gene — maintains population diversity
    if random.random() < 0.03:
        tool = random.choice(MCP_TOOL_REGISTRY)
        genome.tool_genes[tool] = random.uniform(0.3, 0.7)

    genome.mutation_rate = clamp(
        genome.mutation_rate + random.uniform(-0.01, 0.01), 0.01, 0.4
    )


# ── Crossover ─────────────────────────────────────────────────────────────────

def crossover(a: Genome, b: Genome) -> Genome:
    """
    Produce a child genome by inheriting from both parents.
    crossover_stability controls blend (average) vs exploration (pick-one).
    Calls normalize_affinities() so child domain competition is valid.
    """
    stability = (a.crossover_stability + b.crossover_stability) / 2

    def inherit(va: float, vb: float) -> float:
        return (va + vb) / 2 if random.random() < stability else random.choice([va, vb])

    child = Genome(
        model_tier          = inherit(a.model_tier,        b.model_tier),
        temperature         = inherit(a.temperature,       b.temperature),
        reasoning_depth     = inherit(a.reasoning_depth,   b.reasoning_depth),
        verbosity           = inherit(a.verbosity,         b.verbosity),
        coding_affinity     = inherit(a.coding_affinity,   b.coding_affinity),
        research_affinity   = inherit(a.research_affinity, b.research_affinity),
        upwork_affinity     = inherit(a.upwork_affinity,   b.upwork_affinity),
        context_budget      = inherit(a.context_budget,    b.context_budget),
        retrieval_top_k     = inherit(a.retrieval_top_k,   b.retrieval_top_k),
        memory_window       = inherit(a.memory_window,     b.memory_window),
        mutation_rate       = (a.mutation_rate + b.mutation_rate) / 2,
        crossover_stability = stability,
        generation          = max(a.generation, b.generation) + 1,
    )

    child.tool_genes = {
        t: inherit(a.tool_genes.get(t, 0.5), b.tool_genes.get(t, 0.5))
        for t in MCP_TOOL_REGISTRY
    }

    for fname in child.cognition.__dataclass_fields__:
        setattr(child.cognition, fname,
                inherit(getattr(a.cognition, fname), getattr(b.cognition, fname)))

    # Keep affinities competitive in the child
    normalize_affinities(child)

    return child
