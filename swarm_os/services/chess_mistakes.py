"""Learn-from-mistakes + spaced repetition store for the chess trainer.

Research-grounded (2026): the two highest-evidence beginner-improvement
features are (1) error-driven retrieval practice on YOUR OWN blunders and (2)
spaced re-exposure of those failed positions. This store persists every
Mistake/Blunder the learner makes, and re-presents them as 'find the move'
puzzles on a Leitner-style ladder (1d → 3d → 7d → 14d, reset on failure).

Each entry records the position BEFORE the bad move, the bad move itself, the
engine's best move (the answer), the concept, and the book fragment citations
so the review can show the same grounded 'why'. Solves are spaced; a failed
review re-queues the entry at the start of the ladder.

Storage: data/chess_mistakes.jsonl (rolling). Fail-closed: unreadable store
degrades to an empty queue with an error string, never a crash.
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
_STORE_FILE = _DATA_DIR / "mistakes.jsonl"
_LOCK = threading.Lock()

# Spaced-repetition ladder (days). A solve advances to the next box; a failed
# review resets to box 0. Configurable via env for testing.
_SR_LADDER_DAYS = [1, 3, 7, 14]
_MAX_ENTRIES = 500


def _ladder_days() -> list[int]:
    raw = __import__("os").environ.get("CHESS_SR_LADDER", "")
    if raw:
        try:
            days = [int(x) for x in raw.split(",") if x.strip().isdigit()]
            if days:
                return days
        except ValueError:
            pass
    return _SR_LADDER_DAYS


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def _load() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        if _STORE_FILE.exists():
            for line in _STORE_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        log.warning("chess mistakes store load failed: %s", exc)
    return entries


def _save(entries: list[dict[str, Any]]) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _STORE_FILE.open("w", encoding="utf-8") as fh:
            for e in entries[-_MAX_ENTRIES:]:
                fh.write(json.dumps(e) + "\n")
    except Exception as exc:
        log.warning("chess mistakes store save failed: %s", exc)


def record_mistake(
    pre_fen: str,
    played_uci: str,
    played_san: str,
    best_uci: str | None,
    best_san: str | None,
    classification: str,
    concept: str = "",
    book_titles: list[str] | None = None,
) -> dict[str, Any]:
    """Persist a Mistake/Blunder. Returns the stored entry. Deduplicates by
    (pre_fen, played_uci) so the same blunder isn't queued twice."""
    now = _now()
    key = f"{pre_fen}|{played_uci}"
    with _LOCK:
        entries = _load()
        existing = next((e for e in entries if e.get("key") == key), None)
        if existing:
            # Already queued: refresh the timestamp but keep the box so the
            # learner isn't spammed with the same position repeatedly.
            existing["last_seen"] = now
            _save(entries)
            return existing
        entry: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "key": key,
            "pre_fen": pre_fen,
            "played_uci": played_uci,
            "played_san": played_san,
            "best_uci": best_uci,
            "best_san": best_san,
            "classification": classification,
            "concept": concept,
            "book_titles": book_titles or [],
            "box": 0,
            "due_at": now,
            "last_seen": now,
            "solves": 0,
            "fails": 0,
        }
        entries.append(entry)
        _save(entries)
        return entry


def review_due(limit: int = 10, box: int | None = None) -> dict[str, Any]:
    """Entries whose due_at has passed (spaced ladder), oldest box first."""
    now = _now()
    with _LOCK:
        entries = _load()
        due = [e for e in entries if e.get("due_at", 0) <= now]
        if box is not None:
            due = [e for e in due if e.get("box", 0) == box]
        due.sort(key=lambda e: (e.get("box", 0), e.get("due_at", 0)))
        return {
            "ok": True,
            "due": due[:limit],
            "total": len(entries),
            "due_count": len(due),
            "ladder_days": _ladder_days(),
        }


def _resolve(entry_id: str) -> dict[str, Any] | None:
    entries = _load()
    return next((e for e in entries if e.get("id") == entry_id), None)


def mark_solved(entry_id: str) -> dict[str, Any]:
    """A correct review: advance the box; if past the ladder, the entry is
    retired. Returns the updated entry (or a retired flag)."""
    ladder = _ladder_days()
    with _LOCK:
        entries = _load()
        entry = next((e for e in entries if e.get("id") == entry_id), None)
        if entry is None:
            return {"ok": False, "error": "entry not found"}
        entry["box"] = min(entry.get("box", 0) + 1, len(ladder))
        entry["solves"] = entry.get("solves", 0) + 1
        retired = entry["box"] >= len(ladder)
        if retired:
            entries.remove(entry)
        else:
            entry["due_at"] = _now() + ladder[entry["box"]] * 86400
        _save(entries)
        return {"ok": True, "box": entry["box"], "retired": retired, "id": entry_id}


def mark_failed(entry_id: str) -> dict[str, Any]:
    """A wrong review: reset to box 0 (due tomorrow)."""
    with _LOCK:
        entries = _load()
        entry = next((e for e in entries if e.get("id") == entry_id), None)
        if entry is None:
            return {"ok": False, "error": "entry not found"}
        entry["box"] = 0
        entry["fails"] = entry.get("fails", 0) + 1
        entry["due_at"] = _now() + _ladder_days()[0] * 86400
        _save(entries)
        return {"ok": True, "box": 0, "id": entry_id}


def stats() -> dict[str, Any]:
    with _LOCK:
        entries = _load()
    boxes: dict[int, int] = {}
    for e in entries:
        boxes[e.get("box", 0)] = boxes.get(e.get("box", 0), 0) + 1
    return {
        "ok": True,
        "total": len(entries),
        "boxes": {str(k): v for k, v in sorted(boxes.items())},
        "ladder_days": _ladder_days(),
    }


def _classify_concept(
    pre_fen: str, played_uci: str, best_uci: str | None, classification: str
) -> str:
    """Deterministically classify a mistake into a coachable error category.

    Uses the position BEFORE the move + the played vs the engine's best move,
    with python-chess structural checks. No LLM — cheap, reproducible, and it
    powers the coach report's weakness profile. Order matters: the most
    specific/actionable category wins.

    Categories (2026 coach taxonomy for ~500):
      hanging piece        - the played move leaves a piece en prise
      missed capture       - there was a safe capture to take; the player didn't
      missed check         - a strong check/mate was available and ignored
      ignored threat       - the opponent threatened something the player ignored
      bad exchange         - the player traded a good piece for a worse one
      king safety          - the king is exposed / castling mishandled
      pawn structure       - a pawn advance/capture created a weakness
      development          - the player failed to develop / mis-placed a piece
      endgame technique    - a technical endgame error
      calculation          - a miscalculation (the played move loses material)
      imprecise / inaccuracy - a missed stronger move (no obvious category)
    """
    try:
        import chess

        b = chess.Board(pre_fen)
    except Exception as exc:
        log.warning("pre_fen parse failed (%s): %s", pre_fen, exc)
        return classification or "inaccuracy"
    try:
        played = chess.Move.from_uci(played_uci)
    except Exception as exc:
        log.warning("played_uci parse failed (%s): %s", played_uci, exc)
        played = None
    if played is None or played not in b.legal_moves:
        return "inaccuracy"
    mover = b.turn
    after = b.copy()
    after.push(played)

    # Material before/after from the MOVER's perspective.
    def _mat(brd, color):
        vals = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9}
        return sum(vals.get(p.symbol().lower(), 0) for sq in chess.SQUARES if (p := brd.piece_at(sq)) and p.color == color)

    before_mat = _mat(b, mover)
    after_mat = _mat(after, mover)

    # Bad exchange: the played move trades a MORE valuable piece for a LESS
    # valuable one (net material loss for the mover).
    if b.is_capture(played) and after_mat < before_mat:
        return "bad exchange"

    # Hanging piece: after the move, one of the mover's non-king pieces is
    # attacked more than defended (opponent can capture it next move).
    for sq in chess.SQUARES:
        p = after.piece_at(sq)
        if not p or p.color != mover or p.piece_type == chess.KING:
            continue
        attackers = after.attackers(not mover, sq)
        defenders = after.attackers(mover, sq)
        if len(attackers) > len(defenders):
            return "hanging piece"

    # Missed a strong check/mate: the best move gives check but the played move doesn't.
    if best_uci:
        try:
            best = chess.Move.from_uci(best_uci)
            if best in b.legal_moves:
                bbest = b.copy()
                bbest.push(best)
                if bbest.is_check() and not after.is_check():
                    return "missed check"
        except Exception as exc:
            log.debug("missed-check detection failed (uci=%s): %s", best_uci, exc)

    # Missed a capture: best move captures material the player passed on.
    if best_uci and not b.is_capture(played):
        try:
            best = chess.Move.from_uci(best_uci)
            if best in b.legal_moves and b.is_capture(best):
                return "missed capture"
        except Exception as exc:
            log.debug("missed-capture detection failed (uci=%s): %s", best_uci, exc)

    # King safety: only flag when the king is genuinely at risk — the best move
    # castles OR the played move leaves the king exposed to attack. Not just
    # "castling was available" (that fires in every opening).
    king_sq = after.king(mover)
    king_attackers = after.attackers(not mover, king_sq)
    best_castled = False
    if best_uci:
        try:
            best = chess.Move.from_uci(best_uci)
            if best in b.legal_moves and best.uci() in ("e1g1", "e1c1", "e8g8", "e8c8"):
                best_castled = True
        except Exception as exc:
            log.debug("castle detection failed (uci=%s): %s", best_uci, exc)
    if king_attackers or best_castled:
        return "king safety"

    # Pawn structure: a pawn move that loses material (opened a weakness).
    if b.piece_at(played.from_square) and b.piece_at(played.from_square).piece_type == chess.PAWN and after_mat < before_mat:
        return "pawn structure"

    # The played move loses material (miscalculation).
    if after_mat < before_mat:
        return "calculation"

    return "imprecise move"


def get_recurring_mistakes(limit: int = 10) -> dict[str, Any]:
    """Aggregate the queued mistakes into the TOP recurring patterns — the
    concepts the learner keeps making. When the stored concept is generic
    ('imported', empty), a deterministic position-based classifier derives the
    error category. Each bucket shows frequency, severity split, and examples."""
    with _LOCK:
        entries = _load()
    if not entries:
        return {"ok": True, "top": [], "total": 0}

    def _signature(e: dict[str, Any]) -> str:
        concept = (e.get("concept") or "").strip()
        if concept and concept != "imported" and concept != "general":
            return concept
        # Position-based deterministic classification (no LLM).
        return _classify_concept(
            e.get("pre_fen", ""),
            e.get("played_uci", ""),
            e.get("best_uci"),
            e.get("classification", ""),
        )

    by_sig: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        sig = _signature(e)
        by_sig.setdefault(sig, []).append(e)

    ranked: list[dict[str, Any]] = []
    for sig, group in by_sig.items():
        severities: dict[str, int] = {}
        for e in group:
            c = e.get("classification") or "Inaccuracy"
            severities[c] = severities.get(c, 0) + 1
        ranked.append(
            {
                "concept": sig,
                "count": len(group),
                "share_pct": round(100.0 * len(group) / len(entries), 1),
                "severity": severities,
                "examples": [
                    {
                        "played_san": e.get("played_san"),
                        "best_san": e.get("best_san"),
                        "pre_fen": e.get("pre_fen"),
                        "classification": e.get("classification"),
                    }
                    for e in group[:2]
                ],
            }
        )
    ranked.sort(key=lambda x: (-x["count"], -x["share_pct"]))
    return {
        "ok": True,
        "total": len(entries),
        "concepts": len(ranked),
        "top": ranked[:max(1, min(int(limit), 25))],
    }


# Skill-group definitions: which error categories map to which coachable skill.
# Used to build the weakness profile + "today's focus" recommendation.
_SKILL_GROUPS: dict[str, tuple[str, str]] = {
    "hanging piece": ("tactics", "You hang pieces — scan for loose pieces and captures before you move."),
    "missed capture": ("tactics", "You miss winning captures — look for captures of undefended pieces."),
    "missed check": ("tactics", "You miss strong checks — check forcing moves before settling."),
    "imprecise move": ("positional", "You pick solid-but-not-best moves — find the plan, not just a safe move."),
    "bad exchange": ("positional", "You trade good pieces for worse ones — compare piece values before capturing."),
    "king safety": ("defense", "Your king gets exposed — castle and keep it safe before attacking."),
    "pawn structure": ("positional", "Your pawn moves create weaknesses — think about what a pawn push leaves behind."),
    "calculation": ("calculation", "You miscalculate — check the opponent's reply before committing."),
    "endgame technique": ("endgame", "Endgame errors cost you — the king is a fighting piece in the endgame."),
    "development": ("positional", "You mis-handle development — bring pieces out before pushing pawns."),
}


def coach_report() -> dict[str, Any]:
    """The personalized weakness profile: skill bars + today's focus.

    Combines the deterministic concept classifier (over every queued mistake)
    into a coachable skill profile (tactics / positional / defense / calculation
    / endgame), ranks the weakest skills, and recommends a 'today's focus'.
    This is the 'personal curriculum' seed — generic, but grounded in the
    learner's actual recurring error types."""
    with _LOCK:
        entries = _load()
    if not entries:
        return {"ok": True, "total": 0, "skills": {}, "focus": "Play and analyze a game first — no mistakes recorded yet."}

    # Classify each entry, then group into skills.
    skill_counts: dict[str, int] = {}
    concept_counts: dict[str, int] = {}
    concept_blunders: dict[str, int] = {}
    concept_positions: dict[str, set] = {}
    for e in entries:
        concept = _classify_concept(
            e.get("pre_fen", ""), e.get("played_uci", ""), e.get("best_uci"), e.get("classification", "")
        )
        concept_counts[concept] = concept_counts.get(concept, 0) + 1
        if e.get("classification") == "Blunder":
            concept_blunders[concept] = concept_blunders.get(concept, 0) + 1
        concept_positions.setdefault(concept, set()).add(e.get("pre_fen", ""))
        group = _SKILL_GROUPS.get(concept, ("positional", ""))[0]
        skill_counts[group] = skill_counts.get(group, 0) + 1

    # Skill bars: higher count = bigger weakness (0-100 scale, relative to the top).
    max_skill = max(skill_counts.values()) if skill_counts else 1
    skills: dict[str, dict] = {}
    for group in ("tactics", "positional", "defense", "calculation", "endgame"):
        count = skill_counts.get(group, 0)
        if count:
            skills[group] = {
                "count": count,
                "share_pct": round(100.0 * count / len(entries), 1),
                "bar": round(100.0 * count / max_skill),
            }

    # Weakness model: frequency x severity x recurrence x trend -> priority.
    # frequency = share of all mistakes; severity = blunder share; recurrence =
    # distinct positions (a recurring pattern, not one-off); trend = unknown
    # until training history exists (0.0 here — the training engine fills it).
    concept_scores: dict[str, dict[str, Any]] = {}
    for concept, count in concept_counts.items():
        frequency = count / len(entries)
        severity = (concept_blunders.get(concept, 0) / count) if count else 0.0
        recurrence = len(concept_positions.get(concept, set())) / count if count else 0.0
        trend = 0.0  # unknown until the training loop accumulates history
        priority = round(frequency * (0.4 + 0.6 * severity) * (0.5 + recurrence) * (1.0 - trend), 4)
        concept_scores[concept] = {
            "frequency": round(frequency, 3),
            "severity": round(severity, 3),
            "recurrence": round(recurrence, 3),
            "trend": trend,
            "priority": priority,
        }

    # Today's focus: the weakest skill (most frequent) -> its coaching line.
    focus_skill = max(skill_counts, key=skill_counts.get)
    focus_concept = max(concept_counts, key=concept_counts.get)
    advice = _SKILL_GROUPS.get(focus_concept, ("", "Keep studying — every review sharpens the skill."))[1]
    return {
        "ok": True,
        "total": len(entries),
        "skills": skills,
        "top_concepts": sorted(concept_counts.items(), key=lambda x: -x[1])[:5],
        "concept_scores": concept_scores,
        "focus_skill": focus_skill,
        "focus": advice,
    }
