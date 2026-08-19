from typing import Any

# A hand-curated library of prototypical tactical motifs.
# These bypass the engine generation to ensure the player sees classic, unambiguous
# examples of core tactical schemas, accelerating pattern recognition.

MOTIFS = [
    {
        "fen": "r3k3/8/2n5/1B6/8/8/8/4K3 w - - 0 1",
        "best_uci": "b5c6",
        "best_san": "Bxc6+",
        "concept": "pin",
        "classification": "Best",
        "instruction": "The knight is pinned to the king — take it for free.",
    },
    {
        # Opposition (verified with python-chess): kings on the e-file 3 ranks
        # apart, WHITE to move. Ke5 steps to make the kings face 2 apart with
        # BLACK to move — white takes the opposition. (The old position had
        # white to move with kings already 2 apart, which means the opponent
        # holds it, and Ke5 didn't even align the kings.)
        "fen": "8/4k3/8/8/4K3/8/8/8 w - - 0 1",
        "best_uci": "e4e5",
        "best_san": "Ke5",
        "concept": "opposition",
        "classification": "Best",
        "instruction": "Take the opposition: step so the kings face with black to move.",
    },
    {
        # Genuine knight fork: Nc7+ hits the black king (e8) AND the rook (a8)
        # in one move (verified with python-chess). The old position was an
        # exchange (Bxc3), not a fork.
        "fen": "r3k3/8/4N3/8/8/8/8/4K3 w - - 0 1",
        "best_uci": "e6c7",
        "best_san": "Nc7+",
        "concept": "fork",
        "classification": "Best",
        "instruction": "The knight forks the king and the rook — take the rook next.",
    },
    {
        "fen": "8/ppk5/2p5/4n3/8/P3R3/1PP2r2/2K5 w - - 0 35",
        "best_uci": "e3e5",
        "best_san": "Rxe5",
        "concept": "hanging piece",
        "classification": "Best",
        "instruction": "Take the hanging piece.",
    },
    {
        "fen": "2r3k1/5ppp/8/8/8/4Q3/1q3PPP/4R1K1 w - - 0 1",
        "best_uci": "e3e8",
        "best_san": "Qe8+",
        "concept": "back rank mate",
        "classification": "Best",
        "instruction": "Find the back rank mate.",
    },
    {
        "fen": "6k1/5ppp/8/8/8/8/4qPPP/6K1 b - - 0 1",
        "best_uci": "e2e1",
        "best_san": "Qe1#",
        "concept": "back rank mate",
        "classification": "Best",
        "instruction": "Find the back rank mate.",
    },
    {
        "fen": "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
        "best_uci": "h5f7",
        "best_san": "Qxf7#",
        "concept": "scholar's mate",
        "classification": "Best",
        "instruction": "Deliver Scholar's Mate.",
    },
    {
        "fen": "r1bqk2r/pppp1ppp/2n5/2b1p3/2B1P1n1/2NP1N2/PPP2PPP/R1BQK2R w KQkq - 1 6",
        "best_uci": "e1g1",
        "best_san": "O-O",
        "concept": "king safety",
        "classification": "Best",
        "instruction": "Castle to safety.",
    },
    {
        "fen": "rnbqkbnr/ppp2ppp/8/3pp3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq d6 0 3",
        "best_uci": "e4d5",
        "best_san": "exd5",
        "concept": "center control",
        "classification": "Best",
        "instruction": "Take the central pawn.",
    },
]


def get_motif_items() -> list[dict[str, Any]]:
    """Converts the MOTIFS list into standard training-item shapes so the
    trainer can serve and answer them like any other item.

    Key shape contract (matches chess_training._build_item + the frontend
    TrainingItem type): solution_uci / solution_san (the move the learner must
    find), concept, stage, pre_fen, prompt. Source is 'motif' so the coach
    report / progress can distinguish curated motifs from own-game mistakes."""
    items = []
    for m in MOTIFS:
        items.append(
            {
                "concept": m["concept"],
                "stage": "reinforce",
                "pre_fen": m["fen"],
                "solution_uci": m["best_uci"],
                "solution_san": m["best_san"],
                "source": "motif",
                "prompt": m.get("instruction", m["concept"]),
                "difficulty": 1,
            }
        )
    return items
