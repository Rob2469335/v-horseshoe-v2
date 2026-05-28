from __future__ import annotations

import copy
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

MCP_TOOL_REGISTRY: List[str] = [
    "web_search",
    "playwright",
    "filesystem",
    "context7",
    "qdrant_recall",
    "code_exec",
]

MODEL_TIERS: Dict[str, str] = {
    "triage": "qwen2.5:3b-instruct",
    "fast": "qwen2.5-coder:7b-16k",
    "heavy": "qwen3:14b",
}

def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))

def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))

def model_distribution(tier: float, smoke: bool = False) -> Dict[str, float]:
    if smoke:
        triage_weight = clamp(1.0 - (tier * 2.0))
        fast_weight = clamp(1.0 - abs((tier - 0.5) * 2.0))
        total = triage_weight + fast_weight + 1e-9
        return {
            MODEL_TIERS["triage"]: triage_weight / total,
            MODEL_TIERS["fast"]: fast_weight / total,
        }

    triage_weight = clamp(1.0 - (tier * 2.0))
    heavy_weight = clamp((tier * 2.0) - 1.0)
    fast_weight = clamp(1.0 - abs((tier - 0.5) * 2.0))
    total = triage_weight + fast_weight + heavy_weight + 1e-9
    return {
        MODEL_TIERS["triage"]: triage_weight / total,
        MODEL_TIERS["fast"]: fast_weight / total,
        MODEL_TIERS["heavy"]: heavy_weight / total,
    }

def sample_model(tier: float, smoke: bool = False) -> str:
    distribution = model_distribution(tier, smoke=smoke)
    roll = random.random()
    cumulative = 0.0
    for model, weight in distribution.items():
        cumulative += weight
        if roll <= cumulative:
            return model
    return MODEL_TIERS["fast"]

@dataclass
class CognitivePolicy:
    decomposition_bias: float = 0.5
    max_subtasks: float = 0.5
    reflection_depth: float = 0.5
    self_critique_bias: float = 0.5
    verification_bias: float = 0.5
    hallucination_sensitivity: float = 0.5
    parallel_tool_calls: float = 0.5
    retry_aggression: float = 0.5
    fallback_model_bias: float = 0.5
    escalation_bias: float = 0.5
    summarization_bias: float = 0.5
    compression_ratio: float = 0.5
    memory_read_bias: float = 0.5
    memory_write_bias: float = 0.5

    def mutate(self, delta: float) -> None:
        for f in self.__dataclass_fields__:
            setattr(self, f, clamp(getattr(self, f) + random.uniform(-delta, delta)))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CognitivePolicy":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

@dataclass
class Genome:
    model_tier: float = 0.5
    temperature: float = 0.5
    reasoning_depth: float = 0.5
    verbosity: float = 0.5
    coding_affinity: float = 0.33
    research_affinity: float = 0.33
    upwork_affinity: float = 0.33
    context_budget: float = 0.5
    retrieval_top_k: float = 0.5
    memory_window: float = 0.5
    mutation_rate: float = 0.1
    crossover_stability: float = 0.5
    parent_id: Optional[str] = None
    generation: int = 0
    cognition: CognitivePolicy = field(default_factory=CognitivePolicy)
    tool_genes: Dict[str, float] = field(default_factory=lambda: {t: random.uniform(0.25, 0.75) for t in MCP_TOOL_REGISTRY})
    lifetime_fitness: float = 0.0
    evaluations: int = 0


    @property
    def timeout_budget(self) -> float:
        # Higher tiers get more time
        return 10.0 + (self.model_tier * 50.0)

    @property
    def model(self) -> str:
        return sample_model(self.model_tier, smoke=getattr(self, "smoke", False))

    @property
    def actual_temperature(self) -> float:
        return round(self.temperature * 1.2, 2)

    @property
    def average_fitness(self) -> float:
        return 0.0 if self.evaluations == 0 else self.lifetime_fitness / self.evaluations

    def active_tools(self, seed: Optional[int] = None) -> List[str]:
        active = []
        for tool, weight in self.tool_genes.items():
            if random.random() < sigmoid((weight - 0.5) * 6):
                active.append(tool)
        return active

    def record_fitness(self, value: float) -> None:
        self.lifetime_fitness += value
        self.evaluations += 1

    def copy(self, new_parent_id: str) -> "Genome":
        child = copy.deepcopy(self)
        child.parent_id = new_parent_id
        child.generation += 1
        child.lifetime_fitness = 0.0
        child.evaluations = 0
        normalize_affinities(child)
        return child

    def to_dict(self) -> dict:
        return {
            "model_tier": self.model_tier,
            "model": self.model,
            "temperature": self.temperature,
            "reasoning_depth": self.reasoning_depth,
            "verbosity": self.verbosity,
            "coding_affinity": self.coding_affinity,
            "research_affinity": self.research_affinity,
            "upwork_affinity": self.upwork_affinity,
            "context_budget": self.context_budget,
            "retrieval_top_k": self.retrieval_top_k,
            "memory_window": self.memory_window,
            "mutation_rate": self.mutation_rate,
            "crossover_stability": self.crossover_stability,
            "parent_id": self.parent_id,
            "generation": self.generation,
            "tool_genes": dict(self.tool_genes),
            "cognition": self.cognition.to_dict(),
            "average_fitness": self.average_fitness,
            "lifetime_fitness": self.lifetime_fitness,
            "evaluations": self.evaluations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Genome":
        payload = dict(data)
        cog_data = payload.pop("cognition", {})
        for k in ("model", "average_fitness"):
            payload.pop(k, None)
        genome = cls(**{k: v for k, v in payload.items() if k in cls.__dataclass_fields__})
        genome.cognition = CognitivePolicy.from_dict(cog_data)
        return genome

def mutate(genome: Genome) -> None:
    fitness_modifier = 1.0 - clamp(genome.average_fitness)
    adaptive_delta = clamp(genome.mutation_rate * (0.5 + fitness_modifier), 0.01, 0.5)
    for fname in [
        "model_tier", "temperature", "reasoning_depth", "verbosity",
        "coding_affinity", "research_affinity", "upwork_affinity",
        "context_budget", "retrieval_top_k", "memory_window",
        "crossover_stability",
    ]:
        setattr(genome, fname, clamp(getattr(genome, fname) + random.uniform(-adaptive_delta, adaptive_delta)))
    genome.cognition.mutate(adaptive_delta)
    for tool in genome.tool_genes:
        if random.random() < genome.mutation_rate:
            genome.tool_genes[tool] = clamp(genome.tool_genes[tool] + random.uniform(-adaptive_delta, adaptive_delta))
    if random.random() < 0.03:
        tool = random.choice(MCP_TOOL_REGISTRY)
        genome.tool_genes[tool] = random.uniform(0.3, 0.7)
    genome.mutation_rate = clamp(genome.mutation_rate + random.uniform(-0.01, 0.01), 0.01, 0.4)

    normalize_affinities(genome)
def crossover(a: Genome, b: Genome) -> Genome:
    stability = (a.crossover_stability + b.crossover_stability) / 2
    def inherit(va: float, vb: float) -> float:
        return (va + vb) / 2 if random.random() < stability else random.choice([va, vb])
    child = Genome(
        model_tier=inherit(a.model_tier, b.model_tier),
        temperature=inherit(a.temperature, b.temperature),
        reasoning_depth=inherit(a.reasoning_depth, b.reasoning_depth),
        verbosity=inherit(a.verbosity, b.verbosity),
        coding_affinity=inherit(a.coding_affinity, b.coding_affinity),
        research_affinity=inherit(a.research_affinity, b.research_affinity),
        upwork_affinity=inherit(a.upwork_affinity, b.upwork_affinity),
        context_budget=inherit(a.context_budget, b.context_budget),
        retrieval_top_k=inherit(a.retrieval_top_k, b.retrieval_top_k),
        memory_window=inherit(a.memory_window, b.memory_window),
        mutation_rate=(a.mutation_rate + b.mutation_rate) / 2,
        crossover_stability=stability,
        generation=max(a.generation, b.generation) + 1,
    )
    child.tool_genes = {t: inherit(a.tool_genes.get(t, 0.5), b.tool_genes.get(t, 0.5)) for t in MCP_TOOL_REGISTRY}
    for fname in child.cognition.__dataclass_fields__:
        setattr(child.cognition, fname, inherit(getattr(a.cognition, fname), getattr(b.cognition, fname)))
    normalize_affinities(child)
    return child

def normalize_affinities(genome: Genome) -> Genome:
    total = genome.coding_affinity + genome.research_affinity + genome.upwork_affinity
    if total <= 0:
        genome.coding_affinity = genome.research_affinity = genome.upwork_affinity = 1 / 3
        return genome
    genome.coding_affinity /= total
    genome.research_affinity /= total
    genome.upwork_affinity /= total
    return genome

