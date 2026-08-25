import chess
from swarm_os.services.chess_trainer import _attackers_of


def test_pin_aware_defenders():
    board = chess.Board("4k3/8/8/8/7b/6p1/5P2/4K3 w - - 0 1")
    assert _attackers_of(board, chess.G3, chess.WHITE) == 1, (
        "Pawn on f2 CAN capture on g3 because it stays on the pin ray"
    )

    board = chess.Board("4k3/8/8/8/7b/4p3/5P2/4K3 w - - 0 1")
    assert _attackers_of(board, chess.E3, chess.WHITE) == 0, (
        "Pawn on f2 CANNOT capture on e3 because it leaves the pin ray"
    )


def test_en_passant_bug():
    # Set up an en passant scenario to verify we understand the current behavior.
    # White pawn on e5. Black pawn moves d7-d5. White can capture en passant on d6.
    board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
    # Black pawn on d5.
    # Does white attack d5? Since we fixed the bug, it should correctly report 1!
    assert _attackers_of(board, chess.D5, chess.WHITE) == 1, (
        "En passant captures target the piece correctly."
    )


def test_king_square_safe():
    # If we check attackers of the king, it shouldn't crash.
    board = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
    # Even if it's the king, _attackers_of won't replace the king.
    assert _attackers_of(board, chess.E1, chess.BLACK) == 0
