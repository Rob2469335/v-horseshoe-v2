# swarm_os/kernel/selection.py
"""
Selection engine — composite fitness scoring.

Key fixes from simulation analysis:
  - affinity multiplier rescaled: was (0.5 + affinity) giving 0.55x–1.5x
    Now uses (0.7 + affinity * 0.6) giving 0.70x–1.30x — less extreme
  - softmax temperature added: controls selection pressure
    Low temp = winner-takes-all, High temp = more exploration
  - tournament selection added as alternative to softmax
  - diversity bonus: organisms with rare dominant domain get small bonus
  - stagnation detection: organism that hasn't improved in N evals gets penalty
"""
from __future__ import annotations

import logging
import math
import random
import re
from typing import Any, Dict, List, Optional

from .organism import Organism
from .environment import Environment

log = logging.getLogger(__name__)

DOMAIN_WEIGHTS: Dict[str, List[float]] = {
    "coding":   [0.6, 0.2, 0.2],
    "research": [0.5, 0.3, 0.2],
    "upwork":   [0.4, 0.1, 0.5],
    "general":  [0.5, 0.25, 0.25],
}

# Selection pressure — higher = more exploitation, lower = more exploration
# 1.0 = standard softmax, 0.5 = more uniform, 2.0 = more greedy
SOFTMAX_TEMPERATURE = 1.5


# ── Quality proxies ────────────────────────────────────────────────────────────

def _coding_quality(content: str) -> float:
    if not content:
        return 0.0
    score = 0.0
    if re.search(r"```[\w]*\n", content):   score += 0.5
    if len(content) > 100:                  score += 0.2
    if any(kw in content for kw in ["def ", "class ", "import ", "return ", "async "]):
        score += 0.2
    if not content.lower().startswith("error"): score += 0.1
    return min(1.0, score)


def _research_quality(content: str) -> float:
    if not content:
        return 0.0
    score = 0.0
    if len(content) > 200: score += 0.3
    if len(content) > 500: score += 0.2
    if content.count(". ") >= 3: score += 0.2
    if any(kw in content.lower() for kw in
           ["because", "however", "therefore", "according", "research",
            "study", "evidence", "compared", "difference"]):
        score += 0.2
    if re.search(r"\d{4}", content): score += 0.1
    return min(1.0, score)


def _upwork_quality(content: str) -> float:
    if not content:
        return 0.0
    score = 0.0
    fields = ["budget", "skill", "rating", "client", "fit", "score",
              "recommend", "apply", "pass", "hire", "worth", "avoid"]
    found = sum(1 for f in fields if f in content.lower())
    score += min(0.6, found * 0.08)
    if re.search(r"\d+(\.\d+)?/10|\d+%|\$\d+", content): score += 0.2
    if len(content) > 80: score += 0.2
    return min(1.0, score)


QUALITY_PROXIES = {
    "coding":   _coding_quality,
    "research": _research_quality,
    "upwork":   _upwork_quality,
}


# ── Cognition bonus ────────────────────────────────────────────────────────────

_REFLECTION_PATTERNS = re.compile(
    r"\b(however|actually|on second thought|upon reflection|let me reconsider"
    r"|to revise|i should clarify|more accurately|to be precise)\b",
    re.IGNORECASE,
)
_VERIFICATION_PATTERNS = re.compile(
    r"\b(verify|confirm|double.check|uncertain|not sure|i should note"
    r"|worth checking|cannot confirm|to my knowledge)\b",
    re.IGNORECASE,
)
_SUMMARY_PATTERNS = re.compile(
    r"\b(in summary|to summarize|in conclusion|key points|to recap"
    r"|the main takeaway|overall|in short)\b",
    re.IGNORECASE,
)
_HALLUCINATION_GUARD_PATTERNS = re.compile(
    r"\b(i'm not certain|you may want to verify|as of my knowledge"
    r"|i cannot guarantee|please verify|i may be wrong)\b",
    re.IGNORECASE,
)
_SELF_CRITIQUE_PATTERNS = re.compile(
    r"\b(one weakness|a limitation|this assumes|caveat|drawback"
    r"|that said|alternatively|a potential issue)\b",
    re.IGNORECASE,
)


def _cognition_bonus(action: Dict[str, Any], genome) -> float:
    content = action.get("content", "")
    if not content:
        return 0.0
    cog   = genome.cognition
    bonus = 0.0
    if cog.reflection_depth > 0.65     and _REFLECTION_PATTERNS.search(content):       bonus += 0.05
    if cog.verification_bias > 0.65    and _VERIFICATION_PATTERNS.search(content):     bonus += 0.05
    if cog.summarization_bias > 0.65   and _SUMMARY_PATTERNS.search(content):          bonus += 0.04
    if cog.hallucination_sensitivity > 0.65 and _HALLUCINATION_GUARD_PATTERNS.search(content): bonus += 0.04
    if cog.self_critique_bias > 0.65   and _SELF_CRITIQUE_PATTERNS.search(content):    bonus += 0.04
    return min(0.2, bonus)


# ── Diversity bonus ────────────────────────────────────────────────────────────

def _diversity_bonus(organism: Organism, population: List[Organism]) -> float:
    """
    Small fitness bonus for organisms whose dominant domain affinity
    is rare in the current population. Prevents monoculture.
    """
    def dominant(o: Organism) -> str:
        return max(["coding", "research", "upwork"],
                   key=lambda d: getattr(o.genome, f"{d}_affinity"))

    my_dom = dominant(organism)
    dom_counts = {}
    for o in population:
        d = dominant(o)
        dom_counts[d] = dom_counts.get(d, 0) + 1

    total = len(population)
    if total == 0:
        return 0.0

    my_count = dom_counts.get(my_dom, 1)
    # Rarity bonus: 0.0 if dominant (>50%), up to 0.08 if unique
    rarity = 1.0 - (my_count / total)
    return round(rarity * 0.08, 4)


# ── Stagnation penalty ─────────────────────────────────────────────────────────

def _stagnation_penalty(genome, window: int = 5) -> float:
    """
    Small penalty if average_fitness hasn't improved recently.
    Uses evaluations count as a proxy — if the organism has many evals
    but low average, it's stagnating. Encourages children to displace parents.
    """
    if genome.evaluations < window:
        return 0.0
    # Penalty scales with evaluations — older stagnant organisms penalised more
    if genome.average_fitness < 0.3 and genome.evaluations > 10:
        return 0.05
    return 0.0


# ── Core scoring ───────────────────────────────────────────────────────────────

def score_response(
    action: Dict[str, Any],
    genome,
    task_domain: str,
    human_score: Optional[float] = None,
) -> Dict[str, float]:
    content = action.get("content", "")
    elapsed = float(action.get("elapsed", 30.0))
    error   = action.get("error")
    finish  = action.get("finish_reason", "")

    if error or not content:
        return {"quality": 0.0, "speed": 0.0, "completion": 0.0,
                "cognition_bonus": 0.0, "composite": 0.0}

    quality = (
        max(0.0, min(1.0, human_score))
        if human_score is not None
        else QUALITY_PROXIES.get(task_domain, lambda c: 0.5 if c else 0.0)(content)
    )

    speed      = max(0.0, 1.0 - (elapsed / 120.0))
    completion = 0.0
    if content and len(content) > 20: completion += 0.5
    if finish == "stop":              completion += 0.5
    elif finish == "length":          completion += 0.2

    cog_bonus = _cognition_bonus(action, genome)
    w         = DOMAIN_WEIGHTS.get(task_domain, DOMAIN_WEIGHTS["general"])
    composite = w[0] * quality + w[1] * speed + w[2] * completion + cog_bonus

    return {
        "quality":         round(quality,    4),
        "speed":           round(speed,      4),
        "completion":      round(completion, 4),
        "cognition_bonus": round(cog_bonus,  4),
        "composite":       round(composite,  4),
    }


class SelectionEngine:

    def evaluate(
        self,
        organisms: List[Organism],
        env: Environment,
        actions: Optional[List[Dict[str, Any]]] = None,
        human_scores: Optional[Dict[str, float]] = None,
    ) -> None:
        current_task = getattr(env, "current_task", None)
        task_domain = current_task.domain if current_task else "general"

        for i, o in enumerate(organisms):
            action = actions[i] if (actions and i < len(actions)) else {}
            human  = (human_scores or {}).get(o.id)

            scores    = score_response(action, o.genome, task_domain, human_score=human)
            composite = scores["composite"]

            # ── Affinity multiplier — rescaled to reduce dominance ────────────
            # Old: (0.5 + affinity) → range 0.5x–1.5x  (too extreme)
            # New: (0.7 + affinity * 0.6) → range 0.7x–1.3x (balanced)
            affinity   = getattr(o.genome, f"{task_domain}_affinity", 0.33)
            composite *= (0.7 + affinity * 0.6)

            # ── Environment penalties ─────────────────────────────────────────
            composite -= env.entropy * 0.02
            resource_press_func = getattr(env, "resource_pressure", None)
            res_pressure = resource_press_func() if callable(resource_press_func) else getattr(env, "resource_pressure", 0.0)
            composite -= res_pressure * 0.05

            # ── Diversity bonus — rewards rare domain specialists ─────────────
            composite += _diversity_bonus(o, organisms)

            # ── Stagnation penalty — nudges stuck incumbents ──────────────────
            composite -= _stagnation_penalty(o.genome)

            o.genome.record_fitness(composite)
            o.fitness += composite

            o.memory.write({
                "event":              "evaluation",
                "generation":         env.t,
                "task_domain":        task_domain,
                "scores":             scores,
                "affinity":           round(affinity, 3),
                "fitness_delta":      round(composite, 4),
                "total_fitness":      round(o.fitness, 4),
                "avg_genome_fitness": round(o.genome.average_fitness, 4),
                "model":              action.get("model", o.genome.model),
                "tools_used":         action.get("tools_used", []),
                "elapsed":            action.get("elapsed", 0),
                "total_tokens":       action.get("total_tokens", 0),
                "cognition": {
                    "decomposition": round(o.genome.cognition.decomposition_bias, 3),
                    "reflection":    round(o.genome.cognition.reflection_depth, 3),
                    "verification":  round(o.genome.cognition.verification_bias, 3),
                },
            })

            log.debug(
                "eval org=%s domain=%s q=%.3f spd=%.3f cmp=%.3f cog=%.3f Δfit=%.4f avg=%.4f",
                o.id, task_domain,
                scores["quality"], scores["speed"], scores["completion"],
                scores["cognition_bonus"], composite, o.genome.average_fitness,
            )

    def softmax(self, organisms: List[Organism],
                temperature: float = SOFTMAX_TEMPERATURE) -> List[float]:
        """
        Temperature-scaled softmax over average_fitness.
        Higher temperature = more uniform (exploration).
        Lower temperature = winner-takes-all (exploitation).
        """
        vals = [o.genome.average_fitness * temperature for o in organisms]
        if not vals:
            return []
        m    = max(vals)
        exps = [math.exp(min(v - m, 500)) for v in vals]
        s    = sum(exps) + 1e-9
        return [e / s for e in exps]

    def select(self, organisms: List[Organism], k: int) -> List[Organism]:
        if not organisms:
            return []
        picked = random.choices(organisms, weights=self.softmax(organisms), k=k)
        log.debug("selected parents: %s", [o.id for o in picked])
        return picked

    def tournament_select(self, organisms: List[Organism],
                          k: int, tournament_size: int = 3) -> List[Organism]:
        """
        Alternative: tournament selection.
        Randomly picks tournament_size candidates, returns the best.
        More robust than softmax when population is small.
        """
        selected = []
        for _ in range(k):
            candidates = random.sample(organisms, min(tournament_size, len(organisms)))
            winner = max(candidates, key=lambda o: o.genome.average_fitness)
            selected.append(winner)
        return selected

    def cull(self, organisms: List[Organism], max_size: int) -> List[Organism]:
        organisms.sort(key=lambda o: (-o.genome.average_fitness, o.id))
        if len(organisms) > max_size:
            log.info("culled %d → kept %d", len(organisms) - max_size, max_size)
        return organisms[:max_size]

    def top_organisms(self, organisms: List[Organism], n: int = 3) -> List[Organism]:
        return sorted(organisms, key=lambda o: -o.genome.average_fitness)[:n]
