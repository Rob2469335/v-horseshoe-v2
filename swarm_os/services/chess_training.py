"""Concept-level spaced repetition + transfer training for the chess trainer.

The core design rule: SEPARATE the learning item from the underlying concept.
A player who solves their OWN hanging-piece position from memory has not
learned HANGING_PIECE — they learned that one board. The concept is only
considered learned when it transfers to a NEW position.

Three stages per concept:
  Stage 1 — REPAIR:    the player's actual mistake ("you made this mistake").
  Stage 2 — REINFORCE: a DIFFERENT position testing the same concept.
  Stage 3 — TRANSFER:  yet another DIFFERENT position, concept in disguise
                       ("what should you consider before moving?" — the
                       concept is never named).

Reinforce and Transfer must be STRUCTURALLY different (different FEN) — a
learner who recognizes one board has memorized it, not learned the principle.

Scheduling is concept-level Leitner with STAGE-AWARE ladders: a Transfer item
advances more slowly because it is stronger evidence of learning.

  Repair:    1d -> 3d -> 7d
  Reinforce: 2d -> 5d -> 10d -> 21d
  Transfer:  3d -> 7d -> 14d -> 30d

Mastery is STAGE-SPECIFIC (not an aggregate >=70%):
  repair_mastered    = a Repair item solved repeatedly (>=2 clean, box>=2)
  reinforce_mastered = a Reinforce item solved repeatedly
  transfer_mastered  = a Transfer item solved repeatedly
  concept_mastered   = reinforce_mastered AND transfer_mastered

Weakness is frequency x severity x recurrence x trend (computed in the coach
report); the training scheduler prioritizes by that priority, not by raw count.

Confidence is RECORDED for later calibration but deliberately does NOT drive
the box advance yet (that phase follows the SR/transfer loop).

Storage: data/chess/training.jsonl. Fail-closed: unreadable store degrades to
an empty queue, never a crash.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATA_DIR = Path("data/chess")
_STORE_FILE = _DATA_DIR / "training.jsonl"
_LOCK = threading.Lock()

# Stage-aware spaced-repetition ladders (days).
_LADDER_STAGES: dict[str, list[int]] = {
    "repair": [1, 3, 7],
    "reinforce": [2, 5, 10, 21],
    "transfer": [3, 7, 14, 30],
}
_DEFAULT_LADDER = [1, 2, 4, 7]
_MAX_ENTRIES = 600

STAGE_NAMES = ("repair", "reinforce", "transfer")

# Which mistake concept an item exercises (maps to the coach skill groups).
_CONCEPT_TAGS = {
    "hanging piece": "tactics",
    "missed capture": "tactics",
    "missed check": "tactics",
    "imprecise move": "positional",
    "bad exchange": "positional",
    "king safety": "defense",
    "pawn structure": "positional",
    "calculation": "calculation",
    "endgame technique": "endgame",
    "development": "positional",
}


def _now() -> float:
    return time.time()


def _ladder_for(stage: str) -> list[int]:
    import os

    env = os.getenv("CHESS_SR_LADDER")
    if env:
        try:
            days = [int(x) for x in env.split(",") if x.strip()]
            if days:
                return days
        except Exception:  # noqa: BLE001
            pass
    return _LADDER_STAGES.get(stage, _DEFAULT_LADDER)


def _load() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        if _STORE_FILE.exists():
            for line in _STORE_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        log.warning("training store load failed: %s", exc)
    return items


def _save(items: list[dict[str, Any]]) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _STORE_FILE.open("w", encoding="utf-8") as fh:
            for it in items[-_MAX_ENTRIES:]:
                fh.write(json.dumps(it) + "\n")
    except Exception as exc:
        log.warning("training store save failed: %s", exc)


# ---------------------------------------------------------------------------
# Item builders
# ---------------------------------------------------------------------------
def _build_item(
    concept: str,
    stage: str,
    pre_fen: str,
    solution_uci: str,
    solution_san: str,
    source: str,
    prompt: str,
    difficulty: int = 1,
    source_ref: str = "",
) -> dict[str, Any]:
    now = _now()
    return {
        "id": uuid.uuid4().hex[:12],
        "concept": concept,
        "skill": _CONCEPT_TAGS.get(concept, "positional"),
        "stage": stage,
        "pre_fen": pre_fen,
        "solution_uci": solution_uci,
        "solution_san": solution_san,
        "source": source,          # "own_game" | "gm"
        "source_ref": source_ref,  # mistake id / "game:ply"
        "prompt": prompt,
        "difficulty": difficulty,
        "box": 0,
        "due_at": now,
        "last_seen": now,
        "attempts": 0,
        "corrects": 0,
        "clean_solves": 0,          # consecutive corrects with no intervening miss
        "mastered": False,
        "confidence_history": [],
        "correct_history": [],
    }


def build_items_from_mistakes(force: bool = False) -> dict[str, Any]:
    """Create REPAIR items from the classified mistake store (one per unique
    mistake), skipping duplicates already in the training store."""
    from . import chess_mistakes as cm

    with _LOCK:
        existing = _load()
        existing_keys = {it.get("source_ref") for it in existing if it.get("stage") == "repair"}
    new_items: list[dict[str, Any]] = []
    for e in cm._load():
        if e.get("id") in existing_keys:
            continue
        concept = cm._classify_concept(
            e.get("pre_fen", ""), e.get("played_uci", ""), e.get("best_uci", ""), e.get("classification", "")
        )
        if not concept or concept == "imprecise move":
            continue  # imprecise moves are not a clean training concept
        pre_fen = e.get("pre_fen", "")
        solution_uci = e.get("best_uci", "")
        solution_san = e.get("best_san", "")
        if not pre_fen or not solution_uci:
            continue
        new_items.append(
            _build_item(
                concept,
                "repair",
                pre_fen,
                solution_uci,
                solution_san,
                "own_game",
                "You made this mistake. What should you play instead?",
                difficulty=2,
                source_ref=e.get("id"),
            )
        )
    with _LOCK:
        merged = _load() + new_items
        _save(merged)
    return {"ok": True, "created": len(new_items)}


def build_items_from_gm(force: bool = False) -> dict[str, Any]:
    """Create REINFORCE + TRANSFER items from GM critical moments.

    For each concept, GM critical moments are collected and de-duplicated by
    FEN. The FIRST distinct moment becomes a Reinforce item; a SECOND,
    structurally different moment becomes the Transfer item. If only one
    distinct moment exists, only Reinforce is created (Transfer waits until
    more positions are available) — so Reinforce and Transfer are NEVER the
    same position."""
    from . import gm_games as gg
    import chess

    with _LOCK:
        existing = _load()
        existing_refs = {(it.get("source_ref"), it.get("stage")) for it in existing if it.get("source") == "gm"}

    # Collect distinct critical moments per concept.
    moments_by_concept: dict[str, list[dict[str, Any]]] = {}
    for gid, info in gg._load_games_cache().items():
        b = chess.Board()
        for ply, san in enumerate(info["moves"]):
            cm_ = gg._critical_moment(b, san, ply)
            b.push_san(san)
            if not cm_["think_required"]:
                continue
            concept = _map_gm_moment_to_concept(cm_["critical_type"])
            if not concept:
                continue
            # Rebuild the position BEFORE this move.
            b2 = chess.Board()
            for earlier in info["moves"][:ply]:
                b2.push_san(earlier)
            mv = b2.parse_san(san)
            fen = b2.fen()
            # De-dupe by FEN so Reinforce/Transfer are structurally different.
            if not any(m["fen"] == fen for m in moments_by_concept.get(concept, [])):
                moments_by_concept.setdefault(concept, []).append(
                    {
                        "fen": fen,
                        "uci": mv.uci(),
                        "san": san,
                        "difficulty": cm_.get("difficulty", 2),
                        "ref": f"{gid}:{ply}",
                    }
                )

    new_items: list[dict[str, Any]] = []
    for concept, moments in moments_by_concept.items():
        # Reinforce from the first distinct position.
        if moments and (moments[0]["ref"], "reinforce") not in existing_refs:
            m = moments[0]
            new_items.append(
                _build_item(
                    concept, "reinforce", m["fen"], m["uci"], m["san"], "gm",
                    "Same idea, different game — find the strongest move.",
                    difficulty=m["difficulty"], source_ref=m["ref"],
                )
            )
            existing_refs.add((m["ref"], "reinforce"))
        # Transfer from a DIFFERENT position (a different FEN/moment).
        # Prefer the structurally most different moment (last distinct).
        transfer_candidates = [m for m in moments if m["fen"] != moments[0]["fen"]] if moments else []
        if transfer_candidates:
            t = transfer_candidates[-1]
            if (t["ref"], "transfer") not in existing_refs:
                new_items.append(
                    _build_item(
                        concept, "transfer", t["fen"], t["uci"], t["san"], "gm",
                        "What should you consider before moving here? Play the move you think is best.",
                        difficulty=min(3, t["difficulty"] + 1), source_ref=t["ref"],
                    )
                )
                existing_refs.add((t["ref"], "transfer"))
    with _LOCK:
        merged = _load() + new_items
        _save(merged)
    return {"ok": True, "created": len(new_items)}


def _map_gm_moment_to_concept(critical_type: list[str]) -> str | None:
    """Map a GM critical-moment type to a training concept."""
    if "sacrifice" in critical_type or "tactical" in critical_type:
        return "calculation"
    if "defense" in critical_type:
        return "hanging piece"  # a rescued en-prise piece = don't hang pieces
    if "capture" in critical_type:
        return "missed capture"
    if "check" in critical_type:
        return "missed check"
    if "endgame" in critical_type:
        return "endgame technique"
    return None


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
def training_due(limit: int = 10, concept: str | None = None) -> dict[str, Any]:
    """Due training items, ordered by the coach's weakness priority (the
    weakest concept first), then by box (oldest box first). When a specific
    concept is given, only its due items are returned."""
    now = _now()
    with _LOCK:
        items = _load()
    if concept:
        pool = [it for it in items if it.get("concept") == concept and it.get("due_at", 0) <= now and not it.get("retired")]
    else:
        # Weakness priority drives scheduling (frequency x severity x recurrence
        # x trend), not raw count.
        from . import chess_mistakes as cm

        report = cm.coach_report()
        scores = report.get("concept_scores", {})
        def _prio(it):
            c = it.get("concept", "")
            return (scores.get(c, {}).get("priority", 0.0), it.get("box", 0), it.get("due_at", 0))
        pool = [it for it in items if it.get("due_at", 0) <= now and not it.get("retired")]
        pool.sort(key=_prio, reverse=True)  # highest priority (weakest) first
    if not concept:
        # Within equal priority, older box + sooner due first.
        pool.sort(key=lambda it: (it.get("box", 0), it.get("due_at", 0)))
    return {"ok": True, "due": pool[:limit], "due_count": len(pool), "total": len(items)}


def record_answer(item_id: str, correct: bool, confidence: str | None = None) -> dict[str, Any]:
    """Record an answer and advance/fall back the item on its STAGE-AWARE
    ladder. A correct solve advances one box; a wrong answer resets to box 0.
    An item is 'mastered' when it has TWO clean solves and has reached box >= 2
    (repeated success, not a single lucky hit). Confidence is recorded for
    later calibration but does not drive the box advance."""
    with _LOCK:
        items = _load()
        it = next((x for x in items if x.get("id") == item_id), None)
        if it is None:
            return {"ok": False, "error": "no such training item"}
        ladder = _ladder_for(it.get("stage", "repair"))
        it["attempts"] = (it.get("attempts", 0) or 0) + 1
        it["correct_history"] = (it.get("correct_history", []) or []) + [bool(correct)]
        it["confidence_history"] = (it.get("confidence_history", []) or []) + [confidence]
        if correct:
            it["corrects"] = (it.get("corrects", 0) or 0) + 1
            it["clean_solves"] = (it.get("clean_solves", 0) or 0) + 1
            box = it.get("box", 0) + 1
            # Mastered after REPEATED clean solves (>=2) at box >= 2 — a couple
            # of spaced successes, not one lucky hit. Independent of ladder
            # length so short test ladders behave like production.
            if it.get("clean_solves", 0) >= 2 and box >= 2:
                it["mastered"] = True
                it["retired"] = True
                it["due_at"] = 0  # never due again
                _save(items)
                return {"ok": True, "retired": True, "mastered": True, "item": dict(it), "concept": it["concept"], "stage": it["stage"]}
            if box >= len(ladder):
                # Cleared the ladder but not enough clean solves yet — hold at
                # the top box rather than retire without mastery.
                box = len(ladder) - 1
            it["box"] = box
            it["due_at"] = _now() + ladder[box - 1] * 86400 if box > 0 else _now() + 3600
        else:
            it["box"] = 0
            it["clean_solves"] = 0
            it["due_at"] = _now() + 3600  # retry soon after a miss
        it["last_seen"] = _now()
        _save(items)
        return {"ok": True, "retired": False, "mastered": bool(it.get("mastered")), "item": dict(it)}


def concept_progress() -> dict[str, Any]:
    """Per-concept learning stats + STAGE-SPECIFIC mastery flags.

    concept_mastered = reinforce_mastered AND transfer_mastered (repair alone
    proves recall of the original board, not understanding)."""
    with _LOCK:
        items = _load()
    by_concept: dict[str, dict[str, Any]] = {}
    for it in items:
        c = it.get("concept", "?")
        entry = by_concept.setdefault(c, {
            "repair": 0, "reinforce": 0, "transfer": 0,
            "repair_mastered": False, "reinforce_mastered": False, "transfer_mastered": False,
            "attempts": 0, "corrects": 0,
        })
        stage = it.get("stage")
        if stage in entry:
            entry[stage] += 1
            flag = f"{stage}_mastered"
            if it.get("mastered"):
                entry[flag] = True
        entry["attempts"] += it.get("attempts", 0)
        entry["corrects"] += it.get("corrects", 0)
    out = {}
    for c, v in by_concept.items():
        concept_mastered = bool(v.get("reinforce_mastered") and v.get("transfer_mastered"))
        rate = round(100.0 * v["corrects"] / max(1, v["attempts"]), 1) if v["attempts"] else 0.0
        status = "mastered" if concept_mastered else ("seeding" if v["repair"] + v["reinforce"] + v["transfer"] == 0 else "practicing")
        out[c] = {**v, "success_rate": rate, "concept_mastered": concept_mastered, "mastery": status}
    return {"ok": True, "concepts": out, "total_items": len(items)}


def reset_all() -> dict[str, Any]:
    with _LOCK:
        if _STORE_FILE.exists():
            _STORE_FILE.unlink()
    return {"ok": True, "reset": True}


# ---------------------------------------------------------------------------
# Confidence calibration (observation layer only — NEVER drives scheduling)
# ---------------------------------------------------------------------------
# The design invariant: observed performance outranks self-reported confidence.
# Confidence is ANALYZED, never used to skip or reorder training. A user saying
# "I'm 100% sure" must never cause a genuinely-weak concept to be dropped.
_MIN_CALIBRATION_SAMPLES = 5  # below this, a calibration estimate is unreliable

_CONFIDENCE_LEVELS = ("guess", "idea", "confident")


def calibration_report() -> dict[str, Any]:
    """Compute confidence-vs-performance calibration from recorded history.

    For each concept and stage, groups attempts by the confidence recorded
    BEFORE the reveal, then computes the ACTUAL clean-solve rate per level.

    Interpretation per (concept, stage, confidence):
      well-calibrated  - high confidence -> high solve rate, low -> low
      overconfident    - high confidence -> LOW solve rate ("I know it" but don't)
      underconfident   - low confidence -> HIGH solve rate (know it but doubt)
      insufficient     - fewer than _MIN_CALIBRATION_SAMPLES attempts

    This is ANALYTICS ONLY. Scheduling stays driven by the weakness model
    (frequency x severity x recurrence x trend)."""
    with _LOCK:
        items = _load()

    # Aggregate: (concept, stage, confidence) -> {n, corrects}
    agg: dict[tuple[str, str, str], dict[str, int]] = {}
    for it in items:
        concept = it.get("concept", "?")
        stage = it.get("stage", "repair")
        conf_hist = it.get("confidence_history", []) or []
        corr_hist = it.get("correct_history", []) or []
        for conf, correct in zip(conf_hist, corr_hist):
            level = conf if conf in _CONFIDENCE_LEVELS else "idea"
            key = (concept, stage, level)
            cell = agg.setdefault(key, {"n": 0, "corrects": 0})
            cell["n"] += 1
            if correct:
                cell["corrects"] += 1

    # Build the report, concept -> stage -> level.
    by_concept: dict[str, dict[str, Any]] = {}
    for (concept, stage, level), cell in agg.items():
        entry = by_concept.setdefault(concept, {"stages": {}})
        stages = entry["stages"].setdefault(stage, {})
        n = cell["n"]
        rate = round(100.0 * cell["corrects"] / n, 1) if n else 0.0
        if n < _MIN_CALIBRATION_SAMPLES:
            interpretation = "insufficient"
        elif level == "confident":
            interpretation = "well-calibrated" if rate >= 80 else "overconfident"
        elif level == "guess":
            interpretation = "well-calibrated" if rate <= 40 else "underconfident"
        else:  # idea
            interpretation = "well-calibrated" if 40 <= rate <= 80 else ("overconfident" if rate < 40 else "underconfident")
        stages[level] = {
            "n": n,
            "solve_rate": rate,
            "interpretation": interpretation,
        }

    # Per-concept overconfidence flag (the signal that matters for coaching).
    for concept, entry in by_concept.items():
        all_levels = [cell for st in entry["stages"].values() for cell in st.values()]
        flagged = any(c["interpretation"] == "overconfident" for c in all_levels)
        entry["overconfident"] = flagged
    return {"ok": True, "concepts": by_concept, "min_samples": _MIN_CALIBRATION_SAMPLES}
