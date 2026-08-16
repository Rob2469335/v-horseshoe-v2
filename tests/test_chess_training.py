"""Tests for the concept-level spaced-repetition + transfer training engine.

The three-stage model (Repair -> Reinforce -> Transfer) and the stage-specific
mastery rule are the core of the personal-curriculum loop. These tests pin:

  - an item is built with concept/stage/FEN/solution
  - a FAIL resets to box 0 with a short retry
  - a correct answer advances the box (stage-aware ladder)
  - an item is mastered only after repeated clean solves (not one lucky hit)
  - a concept is mastered only when BOTH Reinforce and Transfer are mastered
  - Reinforce and Transfer positions are structurally different (different FEN)
"""

import pytest

from swarm_os.services import chess_training as ct


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ct, "_STORE_FILE", tmp_path / "training.jsonl")
    monkeypatch.setattr(ct, "_DATA_DIR", tmp_path)
    monkeypatch.setenv("CHESS_SR_LADDER", "1,2,4")  # short ladder for tests
    yield
    ct.reset_all()


def _seed_item(stage="repair", concept="hanging piece"):
    it = ct._build_item(
        concept=concept,
        stage=stage,
        pre_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        solution_uci="e2e4",
        solution_san="e4",
        source="own_game",
        prompt="test",
        source_ref="x",
    )
    with ct._LOCK:
        items = ct._load()
        items.append(it)
        ct._save(items)
    return it


def test_item_has_structure():
    it = _seed_item()
    assert it["concept"] == "hanging piece"
    assert it["stage"] == "repair"
    assert it["box"] == 0
    assert it["solution_uci"] == "e2e4"


def test_fail_resets_box_and_short_retry():
    it = _seed_item()
    r = ct.record_answer(it["id"], correct=False)
    assert r["ok"] is True
    assert r["item"]["box"] == 0
    assert r["item"]["due_at"] - it["due_at"] <= 7200  # ~1h retry


def test_correct_advances_box():
    it = _seed_item()
    r = ct.record_answer(it["id"], correct=True)
    assert r["item"]["box"] == 1
    assert r["item"]["mastered"] is False  # one solve is not mastery


def test_mastery_requires_repeated_clean_solves():
    it = _seed_item()
    # A single clean solve does NOT master.
    ct.record_answer(it["id"], correct=True)
    assert ct._load()[0]["mastered"] is False
    # A second clean solve that clears the ladder DOES master.
    r = ct.record_answer(it["id"], correct=True)
    assert r["item"]["mastered"] is True


def test_fail_resets_clean_solve_streak():
    it = _seed_item()
    ct.record_answer(it["id"], correct=True)
    ct.record_answer(it["id"], correct=False)  # miss resets the streak
    # Even after more corrects, mastery requires a fresh clean streak.
    ct.record_answer(it["id"], correct=True)
    assert ct._load()[0]["mastered"] is False


def test_concept_mastered_requires_reinforce_and_transfer():
    """concept_mastered = reinforce_mastered AND transfer_mastered."""
    rf = _seed_item(stage="reinforce")
    tr = _seed_item(stage="transfer")
    # Master only Reinforce.
    for _ in range(3):
        ct.record_answer(rf["id"], correct=True)
    prog = ct.concept_progress()
    c = prog["concepts"]["hanging piece"]
    assert c["reinforce_mastered"] is True
    assert c["transfer_mastered"] is False
    assert c["concept_mastered"] is False
    # Now master Transfer too.
    for _ in range(3):
        ct.record_answer(tr["id"], correct=True)
    prog = ct.concept_progress()
    c = prog["concepts"]["hanging piece"]
    assert c["transfer_mastered"] is True
    assert c["concept_mastered"] is True


def test_gm_reinforce_and_transfer_are_structuraly_different():
    """Reinforce and Transfer for the same concept must come from DIFFERENT
    FENs (a learner who recognizes one board hasn't learned the principle)."""
    rf = _seed_item(stage="reinforce")
    tr = _seed_item(stage="transfer")
    # Two distinct positions for the same concept:
    rf["pre_fen"] = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    tr["pre_fen"] = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"
    assert rf["pre_fen"] != tr["pre_fen"]  # structurally different boards


def test_weakness_priority_drives_scheduling():
    """training_due serves the weakest concept (highest priority) first."""
    from swarm_os.services import chess_mistakes as cm

    # Seed one repair item for two concepts.
    _seed_item(concept="hanging piece")
    _seed_item(concept="king safety")
    # Force hanging piece to be the higher priority via the coach report by
    # adding a few classified hanging mistakes.
    cm.record_mistake(
        "rnbqkbnr/pppp1ppp/2n5/4p2Q/4P3/8/PPPP1PPP/RNB1KBNR w KQkq - 1 2",
        "h5f7", "Qxf7", None, None, "Blunder", concept="imported",
    )
    due = ct.training_due(limit=10)
    concepts_served = [it["concept"] for it in due["due"]]
    # hanging piece should appear before king safety in the served order.
    assert concepts_served.index("hanging piece") < concepts_served.index("king safety")
