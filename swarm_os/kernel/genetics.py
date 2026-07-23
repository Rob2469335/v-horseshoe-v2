from __future__ import annotations

import copy
import math
import random
import json
import ast
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Protocol
from litellm import acompletion

MCP_TOOL_REGISTRY: List[str] = [
    "web_search",
    "playwright",
    "filesystem",
    "context7",
    "code_exec",
]

MODEL_TIERS: Dict[str, str] = {
    "triage": "phi4-mini:latest",
    "fast": "qwen-tuned",
    "heavy": "qwen-tuned",
}


class RandomLike(Protocol):
    def random(self) -> float: ...


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def tool_activation_probability(weight: float) -> float:
    return sigmoid((weight - 0.5) * 6)


def tool_activation_rng(seed: Optional[int] = None) -> RandomLike:
    return random.Random(seed) if seed is not None else random


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
    lifetime_fitness: Dict[str, float] = field(default_factory=lambda: {"composite": 0.0, "quality": 0.0, "speed": 0.0, "efficiency": 0.0})
    evaluations: int = 0
    pareto_rank: int = 0
    crowding_distance: float = 0.0

    @property
    def timeout_budget(self) -> float:
        return max(30.0, 10.0 + (self.model_tier * 50.0))

    @property
    def model(self) -> str:
        return sample_model(self.model_tier, smoke=getattr(self, "smoke", False))

    @property
    def dominant_model(self) -> str:
        dist = model_distribution(self.model_tier, smoke=getattr(self, "smoke", False))
        return max(dist, key=dist.get)

    @property
    def actual_temperature(self) -> float:
        return round(self.temperature * 1.2, 2)

    @property
    def average_fitness(self) -> float:
        return 0.0 if self.evaluations == 0 else self.lifetime_fitness.get("composite", 0.0) / self.evaluations

    @property
    def average_quality(self) -> float:
        return 0.0 if self.evaluations == 0 else self.lifetime_fitness.get("quality", 0.0) / self.evaluations

    @property
    def average_speed(self) -> float:
        return 0.0 if self.evaluations == 0 else self.lifetime_fitness.get("speed", 0.0) / self.evaluations

    @property
    def average_efficiency(self) -> float:
        return 0.0 if self.evaluations == 0 else self.lifetime_fitness.get("efficiency", 0.0) / self.evaluations

    def active_tools(self, seed: Optional[int] = None) -> List[str]:
        rng = tool_activation_rng(seed)
        active: List[str] = []
        for tool, weight in self.tool_genes.items():
            if rng.random() < tool_activation_probability(weight):
                active.append(tool)
        return active

    def record_fitness(self, scores: dict) -> None:
        for k, v in scores.items():
            if k in self.lifetime_fitness:
                self.lifetime_fitness[k] += v
        self.evaluations += 1

    def copy(self, new_parent_id: str) -> "Genome":
        child = copy.deepcopy(self)
        child.parent_id = new_parent_id
        child.generation += 1
        child.lifetime_fitness = {"composite": 0.0, "quality": 0.0, "speed": 0.0, "efficiency": 0.0}
        child.evaluations = 0
        child.pareto_rank = 0
        child.crowding_distance = 0.0
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
            "average_quality": self.average_quality,
            "average_speed": self.average_speed,
            "average_efficiency": self.average_efficiency,
            "lifetime_fitness": dict(self.lifetime_fitness),
            "evaluations": self.evaluations,
            "pareto_rank": self.pareto_rank,
            "crowding_distance": self.crowding_distance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Genome":
        payload = dict(data)
        cog_data = payload.pop("cognition", {})
        for k in ("model", "average_fitness", "average_quality", "average_speed", "average_efficiency"):
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

async def llm_guided_mutate(genome: Genome, trace_context: str, memory_bridge=None) -> None:
    """Uses an LLM to surgically mutate the genome based on failure traces."""
    historical_context = ""
    if memory_bridge:
        try:
            historical_context = await memory_bridge.get_memory_context(trace_context)
        except Exception:
            pass

    history_section = f"\n\nHistorical Context of past runs (GraphRAG):\n{historical_context}\nAvoid repeating past mistakes." if historical_context else ""

    prompt = f'''You are a genetic algorithm mutator for an AI agent. 
The agent failed a task. Review the context and mutate the agent's CognitivePolicy to fix its behavior.
Failure Context: {trace_context}{history_section}

Current Cognitive Policy:
{json.dumps(genome.cognition.to_dict(), indent=2)}

Output a JSON object with the updated cognitive parameters (between 0.0 and 1.0). 
Only include the keys you want to change.
Example: {{"hallucination_sensitivity": 0.8, "verification_bias": 0.9}}
Do not include markdown blocks, just raw JSON.
'''
    try:
        res = await acompletion(
            model=MODEL_TIERS["fast"], 
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        content = res.choices[0].message.content.strip()
        if content.startswith("```json"):
            content = content[7:-3]
        elif content.startswith("```"):
            content = content[3:-3]
        
        patch = json.loads(content.strip())
        for k, v in patch.items():
            if hasattr(genome.cognition, k) and isinstance(v, (int, float)):
                setattr(genome.cognition, k, clamp(float(v), 0.0, 1.0))
    except Exception as e:
        # Fallback to random mutation
        genome.cognition.mutate(0.1)


def ast_slice(source_code: str, target_func: str) -> str:
    """Extracts a precise semantic slice (Program Dependence Graph) of a target function."""
    try:
        tree = ast.parse(source_code)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == target_func:
                return ast.unparse(node)
    except Exception:
        pass
    # BUG FIX: Return empty string instead of the full source_code.
    # If we returned source_code, the caller's core_code.replace(sliced_code, mutated_code)
    # would replace the ENTIRE file with just the mutated function, wiping out the whole engine.
    return ""

async def ast_crossover(parent_a_code: str, parent_b_code: str, target_func: str) -> str:
    """Intelligently splices compatible AST nodes from two parent variants."""
    slice_a = ast_slice(parent_a_code, target_func)
    slice_b = ast_slice(parent_b_code, target_func)
    
    prompt = f'''You are an advanced Genetic Programming AST meta-controller.
We have two successful variants of the function `{target_func}`.
Parent A slice:
```python
{slice_a}
```
Parent B slice:
```python
{slice_b}
```
Intelligently splice the best sub-trees from both variants (e.g. combine a speed optimization from A with a memory optimization from B). Ensure the resulting AST is syntactically valid. Output only the raw python code.
'''
    try:
        res = await acompletion(
            model=MODEL_TIERS["fast"], 
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        content = res.choices[0].message.content.strip()
        if content.startswith("```python"):
            content = content[9:-3]
        elif content.startswith("```"):
            content = content[3:-3]
        
        # Verify it parses correctly
        ast.parse(content.strip())
        return content.strip()
    except Exception:
        return slice_a # fallback to parent A
