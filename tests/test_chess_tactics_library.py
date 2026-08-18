"""Tests for the curated tactical-motif library.

The motif positions are hand-authored prototypes. These tests pin the
non-negotiable invariants: every position is a legal chess position, every
'solution' move is legal for the side to move, and the classified motif is
actually present in the position (e.g. a 'Pin' must be a real pin) — so a
learner is never taught a wrong pattern.
"""

import chess

from swarm_os.services.chess_tactics_library import MOTIFS, get_motif_items


def test_all_motifs_legal_positions_and_solutions():
    for m in MOTIFS:
        b = chess.Board(m["fen"])
        move = chess.Move.from_uci(m["best_uci"])
        assert move in b.legal_moves, f"{m['concept']}: {m['best_uci']} not legal"
        # Side-to-move correctness: the moving piece belongs to the mover.
        from_sq = move.from_square
        piece = b.piece_at(from_sq)
        assert piece is not None and piece.color == b.turn, (
            f"{m['concept']}: {m['best_uci']} moves a non-turn piece"
        )


def test_pin_motif_is_a_real_pin():
    """The Pin prototype must actually be an absolute pin — the knight cannot
    legally move because it exposes the king — and the solution captures the
    pinned piece for free."""
    m = next(x for x in MOTIFS if x["concept"] == "Pin")
    b = chess.Board(m["fen"])
    assert b.is_pinned(chess.BLACK, chess.C6)
    # Bxc6+ wins the pinned knight (black cannot recapture along the pin line).
    b.push_uci(m["best_uci"])
    assert b.is_check()
    recaptures = [mv for mv in b.legal_moves if mv.to_square == chess.C6]
    assert not recaptures, f"pinned piece should not be defensible, got {recaptures}"


def test_motif_items_have_training_shape():
    """get_motif_items must emit the training-item shape the frontend expects
    (solution_uci/san + stage), not a review-entry shape."""
    items = get_motif_items()
    assert len(items) == len(MOTIFS)
    for it in items:
        assert it["solution_uci"]
        assert it["solution_san"]
        assert it["stage"] == "reinforce"
        assert it["source"] == "motif"
        assert it["pre_fen"]
