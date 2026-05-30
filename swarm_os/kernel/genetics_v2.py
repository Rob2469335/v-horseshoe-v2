from __future__ import annotations

import copy
import hashlib
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple


GENOME_SCHEMA_VERSION = 2
PROTOCOL_VERSION = 2

MCP_TOOL_REGISTRY: List[str] = [
    "web_search",
    "playwright",
    "filesystem",
    "context7",
    "qdrant_recall",
    "code_exec",
]


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


@dataclass
class FitnessVector:
    quality: float = 0.0
    cost: float = 0.0
    latency: float = 0.0
    reliability: float = 0.0

    def dominates(self, other: "FitnessVector") -> bool:
        better_or_equal = (
            self.quality >= other.quality and
            self.reliability >= other.reliability and
            self.cost <= other.cost and
            self.latency <= other.latency
        )
        strictly_better = (
            self.quality > other.quality or
            self.reliability > other.reliability or
            self.cost < other.cost or
            self.latency < other.latency
        )
        return better_or_equal and strictly_better


@dataclass
class GenomeStats:
    evaluations: int = 0
    timeout_count: int = 0
    tool_failure_count: int = 0
    malformed_output_count: int = 0
    verify_failure_count: int = 0

    recent_latencies: List[float] = field(default_factory=list)
    recent_executions: List[Tuple[int, float]] = field(default_factory=list)

    SHORT_WINDOW: int = 10
    LONG_WINDOW: int = 100

    def _calculate_percentile(self, data: List[float], percentile: float) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int(math.floor(len(sorted_data) * percentile))
        index = max(0, min(index, len(sorted_data) - 1))
        return sorted_data[index]

    @property
    def latency_p50(self) -> float:
        return self._calculate_percentile(self.recent_latencies, 0.50)

    @property
    def latency_p95(self) -> float:
        return self._calculate_percentile(self.recent_latencies, 0.95)

    @property
    def latency_p99(self) -> float:
        return self._calculate_percentile(self.recent_latencies, 0.99)

    @property
    def short_term_health(self) -> float:
        if not self.recent_executions:
            return 1.0
        short_history = self.recent_executions[-self.SHORT_WINDOW:]
        health_score = 0.0
        for is_success, severity in short_history:
            if is_success:
                health_score += 1.0
            else:
                health_score += (1.0 - severity)
        return health_score / len(short_history)

    def record_execution(self, vector: FitnessVector, status: str = "success") -> None:
        self.evaluations += 1

        severity = 0.0
        if status == "timeout":
            self.timeout_count += 1
            severity = 0.5
        elif status == "malformed":
            self.malformed_output_count += 1
            severity = 0.7
        elif status == "tool_failure":
            self.tool_failure_count += 1
            severity = 1.0
        elif status == "verify_fail":
            self.verify_failure_count += 1
            severity = 0.85

        is_success = 1 if status == "success" else 0
        self.recent_executions.append((is_success, severity))
        self.recent_latencies.append(vector.latency)

        if len(self.recent_latencies) > self.LONG_WINDOW:
            self.recent_latencies.pop(0)
            self.recent_executions.pop(0)


@dataclass
class CognitivePolicy:
    decomposition_bias: float = 0.5
    reflection_depth: float = 0.5
    verification_bias: float = 0.5
    self_critique_bias: float = 0.5
    memory_read_bias: float = 0.5
    memory_write_bias: float = 0.5

    def mutate(self, delta: float, local_rng: random.Random) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, clamp(getattr(self, name) + local_rng.uniform(-delta, delta)))


@dataclass
class ProtocolGenes:
    delegation_depth: float = 0.5
    broadcast_probability: float = 0.5
    peer_review_probability: float = 0.5
    consensus_threshold: float = 0.5

    def mutate(self, delta: float, local_rng: random.Random) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, clamp(getattr(self, name) + local_rng.uniform(-delta, delta)))


@dataclass
class Phenotype:
    route_profile: str
    tool_activation_bias: float
    risk_posture: str
    collaboration_mode: str


@dataclass
class Genome:
    schema_version: int = GENOME_SCHEMA_VERSION
    protocol_version: int = PROTOCOL_VERSION
    genome_id: str = ""
    species_id: str = "default"

    compute_demand: float = 0.5
    reasoning_depth: float = 0.5
    verbosity: float = 0.5
    context_budget: float = 0.5
    retrieval_top_k: float = 0.5
    memory_window: float = 0.5

    mutation_rate: float = 0.10
    crossover_stability: float = 0.5

    cognition: CognitivePolicy = field(default_factory=CognitivePolicy)
    protocol: ProtocolGenes = field(default_factory=ProtocolGenes)
    tool_genes: Dict[str, float] = field(default_factory=dict)

    parent_ids: List[str] = field(default_factory=list)
    generation: int = 0

    def __post_init__(self) -> None:
        if not self.tool_genes:
            self.tool_genes = {t: random.uniform(0.25, 0.75) for t in MCP_TOOL_REGISTRY}
        if not self.genome_id:
            self.genome_id = self.compute_id()

    def compute_id(self) -> str:
        payload = str((
            self.compute_demand,
            self.reasoning_depth,
            self.verbosity,
            self.context_budget,
            self.retrieval_top_k,
            self.memory_window,
            asdict(self.cognition),
            asdict(self.protocol),
            tuple(sorted(self.tool_genes.items())),
        ))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def active_tools(self, seed: Optional[int] = None) -> List[str]:
        local_rng = random.Random(seed) if seed is not None else random.Random()
        active: List[str] = []
        for tool, weight in self.tool_genes.items():
            if local_rng.random() < sigmoid((weight - 0.5) * 6):
                active.append(tool)
        return sorted(active)

    def build_phenotype(self, stats: Optional[GenomeStats] = None) -> Phenotype:
        health = 1.0 if stats is None else stats.short_term_health

        if self.compute_demand < 0.33:
            route_profile = "triage"
        elif self.compute_demand < 0.66:
            route_profile = "default"
        else:
            route_profile = "reasoning"

        if health < 0.60:
            route_profile = "recovery"

        risk_posture = "conservative" if self.cognition.verification_bias >= 0.65 else "balanced"
        if self.protocol.delegation_depth >= 0.67:
            collaboration_mode = "delegative"
        elif self.protocol.peer_review_probability >= 0.67:
            collaboration_mode = "peer_review"
        else:
            collaboration_mode = "direct"

        tool_activation_bias = sum(self.tool_genes.values()) / max(len(self.tool_genes), 1)

        return Phenotype(
            route_profile=route_profile,
            tool_activation_bias=tool_activation_bias,
            risk_posture=risk_posture,
            collaboration_mode=collaboration_mode,
        )

    def copy_child(self) -> "Genome":
        child = Genome(
            compute_demand=self.compute_demand,
            reasoning_depth=self.reasoning_depth,
            verbosity=self.verbosity,
            context_budget=self.context_budget,
            retrieval_top_k=self.retrieval_top_k,
            memory_window=self.memory_window,
            mutation_rate=self.mutation_rate,
            crossover_stability=self.crossover_stability,
            cognition=copy.deepcopy(self.cognition),
            protocol=copy.deepcopy(self.protocol),
            tool_genes=copy.deepcopy(self.tool_genes),
            parent_ids=[self.genome_id],
            generation=self.generation + 1,
        )
        return child


def get_niche_key(genome: Genome) -> Tuple[int, int]:
    niche_x = int(clamp(genome.compute_demand, 0.0, 0.99) * 10)
    niche_y = int(clamp(genome.cognition.reflection_depth, 0.0, 0.99) * 10)
    return (niche_x, niche_y)


class MAPElitesArchive:
    def __init__(self) -> None:
        self.grid: Dict[Tuple[int, int], Tuple[Genome, FitnessVector]] = {}

    def try_add(self, genome: Genome, fitness: FitnessVector) -> bool:
        key = get_niche_key(genome)
        if key not in self.grid:
            self.grid[key] = (copy.deepcopy(genome), fitness)
            return True

        _, incumbent = self.grid[key]
        if fitness.dominates(incumbent):
            self.grid[key] = (copy.deepcopy(genome), fitness)
            return True

        return False

    def get_best_in_niche(self, niche_key: Tuple[int, int]) -> Optional[Genome]:
        return self.grid.get(niche_key, (None, None))[0]


def mutate(genome: Genome, env_stability_signal: float = 1.0, seed: Optional[int] = None) -> None:
    local_rng = random.Random(seed) if seed is not None else random.Random()
    mutation_modifier = clamp(2.0 - env_stability_signal, 0.5, 2.0)
    delta = clamp(genome.mutation_rate * mutation_modifier, 0.02, 0.25)

    for field_name in (
        "compute_demand",
        "reasoning_depth",
        "verbosity",
        "context_budget",
        "retrieval_top_k",
        "memory_window",
        "crossover_stability",
    ):
        setattr(genome, field_name, clamp(getattr(genome, field_name) + local_rng.uniform(-delta, delta)))

    genome.cognition.mutate(delta, local_rng)
    genome.protocol.mutate(delta, local_rng)

    for tool in genome.tool_genes:
        if local_rng.random() < genome.mutation_rate:
            genome.tool_genes[tool] = clamp(genome.tool_genes[tool] + local_rng.uniform(-delta, delta))

    genome.mutation_rate = clamp(genome.mutation_rate + local_rng.uniform(-0.01, 0.01), 0.02, 0.35)
    genome.genome_id = genome.compute_id()


def crossover(a: Genome, b: Genome, seed: Optional[int] = None) -> Genome:
    local_rng = random.Random(seed) if seed is not None else random.Random()
    stability = (a.crossover_stability + b.crossover_stability) / 2.0

    def inherit(x: float, y: float) -> float:
        return (x + y) / 2.0 if local_rng.random() < stability else local_rng.choice([x, y])

    child = Genome(
        compute_demand=inherit(a.compute_demand, b.compute_demand),
        reasoning_depth=inherit(a.reasoning_depth, b.reasoning_depth),
        verbosity=inherit(a.verbosity, b.verbosity),
        context_budget=inherit(a.context_budget, b.context_budget),
        retrieval_top_k=inherit(a.retrieval_top_k, b.retrieval_top_k),
        memory_window=inherit(a.memory_window, b.memory_window),
        mutation_rate=(a.mutation_rate + b.mutation_rate) / 2.0,
        crossover_stability=stability,
        generation=max(a.generation, b.generation) + 1,
        parent_ids=[a.genome_id, b.genome_id],
    )

    child.tool_genes = {
        t: inherit(a.tool_genes.get(t, 0.5), b.tool_genes.get(t, 0.5))
        for t in MCP_TOOL_REGISTRY
    }

    for name in child.cognition.__dataclass_fields__:
        setattr(child.cognition, name, inherit(getattr(a.cognition, name), getattr(b.cognition, name)))

    for name in child.protocol.__dataclass_fields__:
        setattr(child.protocol, name, inherit(getattr(a.protocol, name), getattr(b.protocol, name)))

    child.genome_id = child.compute_id()
    return child
