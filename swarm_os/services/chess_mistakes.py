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


def get_recurring_mistakes(limit: int = 10) -> dict[str, Any]:
    """Aggregate the queued mistakes into the TOP recurring patterns — the
    concepts the learner keeps making. When the stored concept is generic
    ('imported', empty), a lightweight signature is derived from the move text
    (what kind of move was played) so the bucket is still meaningful without
    an LLM. Each bucket shows frequency, severity split, and example moves."""
    with _LOCK:
        entries = _load()
    if not entries:
        return {"ok": True, "top": [], "total": 0}

    def _signature(e: dict[str, Any]) -> str:
        concept = (e.get("concept") or "").strip()
        if concept and concept != "imported" and concept != "general":
            return concept
        # Derive a motif from the played move's algebraic shape.
        san = e.get("played_san") or ""
        played = (e.get("played_uci") or "").lower()
        captured = "x" in san or (len(played) == 4 and played[2] == "x") if isinstance(played, str) else False
        cls = e.get("classification") or ""
        if not san:
            return cls or "general"
        if san.startswith("O"):
            return "castling decision"
        if san.startswith(("+", "K", "Q", "R", "B", "N", "P")) and san.endswith(("+", "#")):
            return "ignored a check / missed the point"
        if captured:
            return "bad capture (hanging the exchange)"
        if cls == "Blunder":
            return "hanging piece / losing blunder"
        if cls == "Mistake":
            return "imprecise move (missed a stronger line)"
        return "inaccuracy"

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
