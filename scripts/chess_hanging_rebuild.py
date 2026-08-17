"""Deterministic, engine-free rebuild of the lilrob2 hanging-piece evidence.

Why this exists
---------------
The analysis job (data/chess/analysis_jobs/0c61bb5b174c.json) analyzed all 842
games with Stockfish and queued 14,561 mistakes into chess_mistakes. The
training store then capped at 500 entries, so the baseline evidence
(data/chess/baseline/full_evidence_842games.json) reports a CAP-INHERITED
count: hanging piece 224 of 500 — a view of the tail, not of the real
population. This script rebuilds the hanging-piece evidence directly from the
source of truth (the 18 Chess.com archives) with NO engine, so the numbers
reflect every game that was actually played.

What it measures (research-grounded, 2026 deep-research round)
---------------------------------------------------------------
* A "hang" = after a move, one of the mover's non-king pieces is attacked
  more than it is defended (raw `board.attackers` counts — the same seam the
  trainer's `_attackers_of` uses).
* The "real hang" gate = the opponent has a LEGALLY PLAYABLE capture of the
  square that WINS material (SEE). This is the pin-aware correction lichess
  merged for exactly this reason (lila#19100): a piece defended only by a
  pinned piece, or attacked only by a pinned piece, is not actually hangable.
  Raw geometry is law-correct (FIDE 3.1.3) but not outcome-correct, so both
  numbers are reported: `raw_events` (geometry) and `real_events` (gated).
* Mechanism families (the 4-family taxonomy from the research):
    F1 moved piece lands en prise (destination square unsafe)
    F2 vacated square / removed defender (a DIFFERENT piece loses protection)
    F3 poisoned capture / recapture chain (grabbed into defended squares)
    F4 pre-existing looseness never noticed (awareness failure)
* Punishment: for each real hang, scan the actual game continuation — was the
  piece actually captured later in the SAME game (and after how many plies)?
  Punished hangs are the decisive training tier; unpunished hangs still count
  (at ~500 the opponent also misses free pieces, so "not captured" is not
  "move was fine") but are reported separately.

Determinism
-----------
The replay + detection + aggregation is a pure function of the selected game
list. Two runs over the same (cached) archives produce the identical report
and the same SHA-256 — the report records that hash. Archive fetches are
cached to data/chess/baseline/archives/<year>-<month>.json so re-runs are
offline and byte-stable.

Usage
-----
    python scripts/chess_hanging_rebuild.py            # full rebuild (fetch if uncached)
    python scripts/chess_hanging_rebuild.py --offline  # use cached archives only
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chess
import chess.pgn

_JOBS = Path("data/chess/analysis_jobs/0c61bb5b174c.json")
_CACHE_DIR = Path("data/chess/baseline/archives")
_OUT = Path("data/chess/baseline/hanging_rebuild_842games.json")

_PIECE_VALUE = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,  # never counted as a hanging piece; SEE stops at it
}


# ---------------------------------------------------------------------------
# Deterministic core (pure functions — no network, no engine)
# ---------------------------------------------------------------------------

def _attackers(board: chess.Board, sq: int, color: bool) -> int:
    return len(board.attackers(color, sq))


def _defenders(board: chess.Board, sq: int) -> int:
    p = board.piece_at(sq)
    if p is None:
        return 0
    return _attackers(board, sq, p.color)


def _legal_captures(board: chess.Board, sq: int, color: bool) -> list[chess.Move]:
    """Every LEGAL capture of `sq` by `color`. Legal-move membership is the
    pin-aware filter: a pinned piece has no legal move, so it drops out
    naturally."""
    out = []
    for m in board.legal_moves:
        if m.to_square == sq:
            p = board.piece_at(m.from_square)
            if p is not None and p.color == color:
                out.append(m)
    return out


def _see(board: chess.Board, sq: int, attacker_color: bool) -> int:
    """Static exchange evaluation: material an attacker can win by capturing
    `sq`. Only LEGAL captures are considered, so a pinned piece drops out at
    every level (it can neither initiate a capture nor recapture) — the
    lila#19100 pin correction the whole gate exists for. Returns 0 when no
    winning capture sequence exists (not a real hang)."""
    victim = board.piece_at(sq)
    if victim is None or victim.piece_type == chess.KING:
        return 0
    value = _PIECE_VALUE.get(victim.piece_type, 0)
    if value <= 0:
        return 0
    # legal_moves is turn-locked; force the turn so the gate is independent of
    # the incoming board's side-to-move
    board.turn = attacker_color
    caps = _legal_captures(board, sq, attacker_color)
    if not caps:
        return 0  # geometrically attacked but NOT legally capturable (pinned attacker)
    caps.sort(
        key=lambda m: (
            _PIECE_VALUE.get(board.piece_at(m.from_square).piece_type, 99)
            if board.piece_at(m.from_square)
            else 99,
        )
    )
    m = caps[0]
    board.push(m)
    opponent_gain = _see(board, sq, not attacker_color)
    board.pop()
    # max(0, ...) = stand-pat: the capturer can decline a losing continuation.
    return max(0, value - opponent_gain)


@dataclass
class HangEvent:
    move_index: int
    side: str
    square: str
    piece: str
    raw_attackers: int
    raw_defenders: int
    see_gain: int
    family: str
    was_capture: bool
    punished: bool = False
    punishment_plies: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "move_index": self.move_index,
            "side": self.side,
            "square": self.square,
            "piece": self.piece,
            "raw_attackers": self.raw_attackers,
            "raw_defenders": self.raw_defenders,
            "see_gain": self.see_gain,
            "family": self.family,
            "was_capture": self.was_capture,
            "punished": self.punished,
            "punishment_plies": self.punishment_plies,
        }


def _find_hangs_after_move(
    probe: chess.Board,
    mover: bool,
    moved_to: int,
    was_capture: bool,
    pre_board: chess.Board,
) -> list[dict[str, Any]]:
    """Post-move hang scan for the mover. Returns candidate events with raw
    counts + SEE gate. Family priority (deterministic):
        destination hang  -> F1 (moved into an unsafe square) or F3 (poisoned
                             capture: grabbed INTO a defended square)
        pre-existing hang -> F4 (already loose before this move, not addressed)
        else              -> F2 (removed a defender / vacated a square)
    """
    me = mover
    opp = not mover
    events: list[dict[str, Any]] = []
    for sq in chess.SQUARES:
        p = probe.piece_at(sq)
        if not p or p.color != me or p.piece_type == chess.KING:
            continue
        attackers = _attackers(probe, sq, opp)
        defenders = _defenders(probe, sq)
        if attackers <= defenders:
            continue  # not even geometrically loose
        gain = _see(probe.copy(), sq, opp)
        was_hanging_pre = False
        pre_p = pre_board.piece_at(sq)
        if pre_p and pre_p.color == me and pre_p.piece_type != chess.KING:
            was_hanging_pre = _attackers(pre_board, sq, opp) > _defenders(pre_board, sq)

        if sq == moved_to:
            # Destination square — the moved piece itself.
            # Poisoned capture: grabbed INTO a square the opponent defends.
            # Heuristic (deterministic): the opponent defends the destination
            # at least as well as the mover supports the grab (defenders >=
            # the mover's attackers on that square BEFORE the capture).
            if was_capture and _defenders(pre_board, sq) >= _attackers(pre_board, sq, me):
                family = "F3"
            else:
                family = "F1"
        elif was_hanging_pre:
            family = "F4"
        else:
            family = "F2"

        events.append(
            {
                "square": chess.square_name(sq),
                "piece": p.symbol(),
                "raw_attackers": attackers,
                "raw_defenders": defenders,
                "see_gain": gain,
                "family": family,
                "was_capture": was_capture,
                "_sq": sq,
            }
        )
    return events


def replay_game(headers: dict[str, Any], board: chess.Board) -> list[dict[str, Any]]:
    """Engine-free replay of one parsed game. Returns hang-event records.

    Two passes: (1) replay every move, detecting hangs at each ply; (2) for
    each SEE-positive hang, walk the ACTUAL continuation from that hang's own
    ply and mark it punished if the opponent captures the square later in the
    same game."""
    moves = list(board.move_stack)
    pre_boards: list[chess.Board] = []
    events: list[HangEvent] = []
    replay = chess.Board()
    for i, mv in enumerate(moves):
        pre = replay.copy()
        pre_boards.append(pre)
        was_capture = replay.is_capture(mv)
        mover = replay.turn
        replay.push(mv)
        for cand in _find_hangs_after_move(replay, mover, mv.to_square, was_capture, pre):
            ev = HangEvent(
                move_index=i,
                side="white" if mover == chess.WHITE else "black",
                square=cand["square"],
                piece=cand["piece"],
                raw_attackers=cand["raw_attackers"],
                raw_defenders=cand["raw_defenders"],
                see_gain=cand["see_gain"],
                family=cand["family"],
                was_capture=cand["was_capture"],
            )
            events.append(ev)

    # Punishment pass — from EACH event's own hang ply onward. The board before
    # move j is pre_boards[j]. Punished = the opponent captures the hanging
    # side's piece on that square before the hanging side vacates it (an
    # escape = the hang was resolved, not punished).
    for ev in events:
        if ev.see_gain <= 0:
            continue
        sq = chess.parse_square(ev.square)
        hung_side = bool(ev.move_index % 2 == 0)  # white moves on even plies
        for j in range(ev.move_index + 1, len(moves)):
            m = moves[j]
            pre = pre_boards[j]
            if m.from_square == sq:
                own = pre.piece_at(sq)
                if own is not None and own.color == hung_side and not pre.is_capture(m):
                    break  # the hanging piece moved away — hang resolved
            if m.to_square == sq and pre.is_capture(m):
                victim = pre.piece_at(sq)
                if victim is not None and victim.color == hung_side:
                    ev.punished = True
                    ev.punishment_plies = j - ev.move_index
                    break
    return [e.to_dict() for e in events]


def _parse_pgn(pgn_text: str) -> tuple[dict[str, Any], chess.Board] | None:
    try:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None:
            return None
        board = chess.Board()
        for node in game.mainline():
            if node.move is not None:
                board.push(node.move)
        if not board.move_stack:
            return None
        return dict(game.headers), board
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Archive fetch + cache (network layer — the ONLY non-deterministic step)
# ---------------------------------------------------------------------------

def _cache_path(url: str) -> Path:
    parts = url.rstrip("/").split("/")
    return _CACHE_DIR / f"{parts[-2]}-{parts[-1]}.json"


def _fetch_archive(url: str, offline: bool) -> dict[str, Any]:
    cache = _cache_path(url)
    if cache.exists():
        with open(cache, "rb") as f:
            raw = f.read()
        try:
            return json.loads(raw)
        except Exception:
            pass
    if offline:
        raise RuntimeError(f"no cached archive for {url} (offline mode)")
    import httpx

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        raw = resp.content
    # Decompress if chess.com serves gzip.
    try:
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
    except Exception:
        pass
    data = json.loads(raw)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    return data


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Aggregation + report
# ---------------------------------------------------------------------------

def _aggregate(all_events: list[dict[str, Any]]) -> dict[str, Any]:
    real = [e for e in all_events if e["see_gain"] > 0]
    fam = {f: 0 for f in ("F1", "F2", "F3", "F4")}
    fam_punished = {f: 0 for f in ("F1", "F2", "F3", "F4")}
    for e in real:
        fam[e["family"]] += 1
        if e["punished"]:
            fam_punished[e["family"]] += 1
    lat = [e["punishment_plies"] for e in real if e["punished"]]
    by_piece: dict[str, int] = {}
    for e in real:
        by_piece[e["piece"]] = by_piece.get(e["piece"], 0) + 1
    return {
        "real_events": len(real),
        "raw_events": len(all_events),
        "punished_events": len([e for e in real if e["punished"]]),
        "unpunished_events": len([e for e in real if not e["punished"]]),
        "punishment_latency_plies": {
            "min": min(lat) if lat else None,
            "max": max(lat) if lat else None,
            "median": sorted(lat)[len(lat) // 2] if lat else None,
        },
        "family_counts": fam,
        "family_punished": fam_punished,
        "family_ranking": sorted(fam, key=lambda f: -fam[f]),
        "by_piece": dict(sorted(by_piece.items(), key=lambda kv: -kv[1])),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use cached archives only")
    ap.add_argument("--out", default=str(_OUT))
    args = ap.parse_args()

    job = json.loads(_JOBS.read_text(encoding="utf-8"))
    refs = list(job["game_refs"])  # (url, index) pairs, job order

    # Group refs by archive so each URL is fetched exactly once, serially.
    by_url: dict[str, list[tuple[int, int]]] = {}
    for gi, (url, idx) in enumerate(refs):
        by_url.setdefault(url, []).append((gi, idx))

    fetched: dict[int, tuple[dict[str, Any], chess.Board]] = {}
    total = len(refs)
    print(f"rebuild: {total} game refs across {len(by_url)} archives")
    all_cached = True
    for url in sorted(by_url):
        cache = _cache_path(url)
        if not cache.exists():
            all_cached = False
        data = _fetch_archive(url, args.offline)
        games = data.get("games", [])
        for gi, idx in by_url[url]:
            if 0 <= idx < len(games):
                parsed = _parse_pgn(games[idx].get("pgn", ""))
                if parsed is not None:
                    fetched[gi] = parsed

    all_events: list[dict[str, Any]] = []
    games_ok = 0
    for gi in range(total):
        parsed = fetched.get(gi)
        if parsed is None:
            continue
        headers, board = parsed
        events = replay_game(headers, board)
        all_events.extend(events)
        games_ok += 1

    agg = _aggregate(all_events)

    payload_lines = [json.dumps(e, sort_keys=True) for e in all_events]
    payload_text = "\n".join(payload_lines) + "\n"
    digest = _sha256(payload_text)

    report = {
        "kind": "hanging_piece_rebuild",
        "username": job["username"],
        "game_refs_total": total,
        "games_replayed": games_ok,
        "archives_fetched": len(by_url),
        "archives_cached": all_cached,
        "method": (
            "engine-free python-chess replay; raw = board.attackers(opp) > "
            "defenders; real = SEE-gated legal capture wins material "
            "(pin-aware, lichess lila#19100); families F1 en-prise / F2 "
            "defender-vacated / F3 poisoned capture / F4 pre-existing looseness; "
            "punished = opponent actually captured the square later in the SAME game"
        ),
        "aggregate": agg,
        "payload_sha256": digest,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out), "sha256": digest, **agg}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
