"""Outcome-driven fitness — the bridge between the live agent loop and the
evolutionary kernel (research-grounded, 2024-2026 SOTA).

Problem: the genetic kernel's organisms scored fitness from LLM chat noise
(diary full of http_422 / WinError artifacts), never from real task outcomes —
so evolution was meaningless. This module captures REAL agent outcomes from the
live step_agent_stream loop and computes a grounded composite fitness.

Composite (per the research synthesis; completion is GATING):
    F = 0.40*completion + 0.25*test_pass + 0.20*tool_success
        + 0.10*efficiency + 0.05*human
    if goal unmet (completion=0): F capped at 0.4.
Tool success is a shaping reward; completion/test_pass are the terminal signal.
Fitness is persisted per genome-id to `data/evolution/fitness.jsonl` so the
kernel can select on real outcomes, and only a frozen/deterministic signal is
used (never self-judged by the proposing model).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

FITNESS_PATH = Path("data/evolution/fitness.jsonl")
_LOCK = threading.Lock()

# Composite weights (research-grounded, completion gating).
_W = {"completion": 0.40, "test_pass": 0.25, "tool_success": 0.20,
      "efficiency": 0.10, "human": 0.05}
_COMPLETION_GATE_CAP = 0.4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_fitness(completion: float = 0.0, test_pass: float = 0.0,
                    tool_success: float = 0.0, efficiency: float = 0.0,
                    human: float = 0.0) -> dict:
    """Compute the composite fitness from real outcome signals. All inputs in
    [0,1]. Completion gates: if the goal was unmet, cap the composite at 0.4 so
    an agent that never finished cannot outrank one that did."""
    completion = max(0.0, min(1.0, completion))
    test_pass = max(0.0, min(1.0, test_pass))
    tool_success = max(0.0, min(1.0, tool_success))
    efficiency = max(0.0, min(1.0, efficiency))
    human = max(0.0, min(1.0, human))

    composite = (
        _W["completion"] * completion
        + _W["test_pass"] * test_pass
        + _W["tool_success"] * tool_success
        + _W["efficiency"] * efficiency
        + _W["human"] * human
    )
    if completion < 0.5:
        composite = min(composite, _COMPLETION_GATE_CAP)

    return {
        "composite": round(composite, 4),
        "quality": round(completion, 4),
        "speed": round(efficiency, 4),
        "efficiency": round(efficiency, 4),
        "test_pass": round(test_pass, 4),
        "tool_success": round(tool_success, 4),
    }


def record_outcome(genome_id: str, *, completion: float = 0.0,
                   test_pass: float = 0.0, tool_success: float = 0.0,
                   efficiency: float = 0.0, human: float = 0.0,
                   task: str = "", agent_id: str = "") -> dict:
    """Persist a real outcome for a genome and return the computed fitness.
    Thread-safe append to data/evolution/fitness.jsonl. Never raises."""
    try:
        fitness = compute_fitness(completion, test_pass, tool_success, efficiency, human)
        record = {
            "ts": _now(),
            "genome_id": genome_id,
            "agent_id": agent_id or genome_id,
            "task": str(task)[:200],
            "completion": completion,
            "test_pass": test_pass,
            "tool_success": tool_success,
            "efficiency": efficiency,
            "human": human,
            "fitness": fitness,
        }
        with _LOCK:
            FITNESS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(FITNESS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return fitness
    except Exception as exc:
        log.debug("outcome fitness record skipped: %s", exc)
        return compute_fitness(completion, test_pass, tool_success, efficiency, human)


def best_fitness(genome_id: str, window: int = 20) -> float | None:
    """Best composite fitness recorded for a genome in the recent window. Used by
    the evolution daemon to select on real outcomes (elitism)."""
    if not FITNESS_PATH.exists():
        return None
    best = None
    try:
        with open(FITNESS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-window:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("genome_id") == genome_id:
                f = rec.get("fitness", {}).get("composite")
                if f is not None and (best is None or f > best):
                    best = f
    except OSError:
        return None
    return best


def recent_fitness(limit: int = 100) -> list[dict]:
    """Last N fitness records, newest last. For dashboards / tests."""
    if not FITNESS_PATH.exists():
        return []
    out = []
    try:
        with open(FITNESS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def genome_has_fitness(genome_id: str) -> bool:
    return best_fitness(genome_id) is not None


def best_aggregate_fitness(window: int = 20) -> float | None:
    """Best composite fitness across ALL recent agent outcomes.

    The evolution population is a single shared tool-policy lineage consumed by
    every agent (one `_best_genome_tool_weights()` result reordered into each
    agent's allowed-tool list). Agent outcomes are keyed `agent:<id>` in
    fitness.jsonl, which never equals the population's `genome_<n>` ids, so an
    exact-ID `best_fitness()` lookup returns None and every genome scores the
    flat 0.05 prior (frozen population, 0.0425 elite plateau). Aggregate over
    all outcomes so the real signal reaches the kernel.
    """
    if not FITNESS_PATH.exists():
        return None
    best = None
    try:
        with open(FITNESS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-window:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            f = rec.get("fitness", {}).get("composite")
            if f is not None and (best is None or f > best):
                best = f
    except OSError:
        return None
    return best


def _fitness_env_enabled() -> bool:
    """Opt-in gate for feeding real outcomes to the kernel. Default ON when
    SWARM_EVOLUTION=1, else off (keeps the runtime lean; no overhead when the
    kernel isn't being evolved)."""
    return os.environ.get("SWARM_EVOLUTION", "").strip() == "1"
