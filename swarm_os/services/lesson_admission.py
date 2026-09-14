"""Lesson admissibility gate — decide whether a retrieved lesson may shape a decision.

Today retrieval injects a hint on similarity alone; stale/wrong lessons can actively
hurt ("reflexion rot"), and LLM self-evaluation is unreliable. This module is a
DETERMINISTIC, rule-based gate (no LLM judge) run BEFORE a lesson is injected.

Grounding:
- Meta-Policy Reflexion (arXiv:2509.03990): explicit rule-admissibility test.
- Dynamic Agent Skills (arXiv:2607.10113): collect evidence -> propose -> VERIFY and admit.
- Governed memory as selection (arXiv:2605.04264): which memory becomes shared state.
- Oblivion (arXiv:2604.00131) / FinMem (arXiv:2311.13743): decayed accessibility.
- Learning When to Remember (arXiv:2604.27283): prefer ABSTENTION over a weak injection.

Pure: no I/O, no network. The caller (reflection_loop) supplies candidates + context.
"""

from __future__ import annotations

DEFAULTS = {
    "min_confidence": 0.6,   # raw confidence floor
    "min_success": 1,        # must have >=1 verified success (evidence, not just belief)
    "half_life_days": 30.0,  # recency decay half-life
    "decay_floor": 0.3,      # decayed confidence floor after ageing
    "top_k": 3,              # max lessons injected
}


def decayed_confidence(cand: dict, now: float, half_life_days: float) -> float:
    """confidence * 0.5 ** (age_days / half_life). Age from ts/created_at (epoch seconds)."""
    conf = float(cand.get("confidence", 0.0) or 0.0)
    ts = cand.get("ts") or cand.get("created_at")
    if not ts:
        return conf  # no timestamp -> no decay (do not silently punish)
    try:
        age_days = max(0.0, (float(now) - float(ts)) / 86400.0)
    except (TypeError, ValueError):
        return conf
    return conf * (0.5 ** (age_days / max(1e-9, half_life_days)))


def admit(
    cand: dict,
    context: dict,
    now: float,
    *,
    min_confidence: float = DEFAULTS["min_confidence"],
    min_success: int = DEFAULTS["min_success"],
    half_life_days: float = DEFAULTS["half_life_days"],
    decay_floor: float = DEFAULTS["decay_floor"],
) -> dict:
    """Single-candidate admissibility. Returns {admitted, decayed, reasons}."""
    reasons: list[str] = []

    # 1. scope/component applicability
    component = (cand.get("component") or "").strip().lower()
    scope = (cand.get("scope") or "agent").strip().lower()
    agent = (context.get("agent_id") or "").strip().lower()
    if component and agent and component != agent and scope != "shared":
        reasons.append(f"component '{component}' != agent '{agent}' and not shared")

    # 2. tool relevance — a lesson about tool T should only fire when T is in play
    warned_tools = set(cand.get("warned_tools") or [])
    ctx_tools = set(context.get("tools") or [])
    if warned_tools and ctx_tools and not (warned_tools & ctx_tools):
        reasons.append(f"warned_tools {sorted(warned_tools)} not in play {sorted(ctx_tools)}")

    # 3. raw confidence
    conf = float(cand.get("confidence", 0.0) or 0.0)
    if conf < min_confidence:
        reasons.append(f"confidence {conf:.2f} < {min_confidence}")

    # 4. verified evidence (not just a belief)
    succ = int(cand.get("success_count", 0) or 0)
    if succ < min_success:
        reasons.append(f"success_count {succ} < {min_success}")

    # 5. recency decay
    dec = decayed_confidence(cand, now, half_life_days)
    if dec < decay_floor:
        reasons.append(f"decayed {dec:.2f} < floor {decay_floor}")

    return {"admitted": not reasons, "decayed": round(dec, 3), "reasons": reasons}


def admit_all(
    candidates: list[dict],
    context: dict,
    now: float,
    *,
    top_k: int = DEFAULTS["top_k"],
    **opts,
) -> dict:
    """Gate a candidate set: per-candidate admission -> conflict resolution -> top-k.

    Abstains (empty) rather than injecting a weak/conflicting lesson. A candidate may
    declare `conflicts_with: [lesson_id, ...]`; the LOWER-evidence side is dropped.
    (Automatic conflict DETECTION is a follow-up — explicit declaration is deterministic.)
    """
    passed, rejected = [], []
    for cand in candidates:
        verdict = admit(cand, context, now, **opts)
        if verdict["admitted"]:
            passed.append({"cand": cand, "decayed": verdict["decayed"]})
        else:
            rejected.append({"lesson_id": cand.get("lesson_id"), "reasons": verdict["reasons"]})

    passed.sort(key=lambda x: x["decayed"], reverse=True)

    # conflict resolution: keep the higher-evidence side of a declared conflict
    kept: list[dict] = []
    dropped_ids: set = set()
    for item in passed:
        lid = item["cand"].get("lesson_id")
        if lid in dropped_ids:
            continue
        conflicts = set(item["cand"].get("conflicts_with") or [])
        for other in passed:
            oid = other["cand"].get("lesson_id")
            if oid in conflicts and other["decayed"] <= item["decayed"]:
                dropped_ids.add(oid)
        kept.append(item)

    admitted = kept[: max(0, top_k)]
    return {
        "admitted": [x["cand"] for x in admitted],
        "rejected": rejected,
        "dropped_conflicts": sorted(dropped_ids),
        "abstained": not admitted,
    }
