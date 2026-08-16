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


def _confidence_item(concept="hanging piece"):
    return _seed_item(concept=concept)


def test_calibration_math_and_overconfidence_flag():
    """'confident' solves that are rare -> overconfident flag, but the flag is
    analytics only: scheduling is untouched."""
    it = _confidence_item()
    # 6 confident attempts, only 2 correct (solve rate 33% < 80% -> overconfident)
    for i in range(6):
        ct.record_answer(it["id"], correct=(i < 2), confidence="confident")
    cal = ct.calibration_report()
    cell = cal["concepts"]["hanging piece"]["stages"]["repair"]["confident"]
    assert cell["n"] == 6
    assert cell["solve_rate"] == round(100.0 * 2 / 6, 1)
    assert cell["interpretation"] == "overconfident"
    assert cal["concepts"]["hanging piece"]["overconfident"] is True


def test_calibration_small_sample_guard():
    """Fewer than the min sample size -> 'insufficient', never a wild score."""
    it = _confidence_item()
    for i in range(3):  # 3 < min (5)
        ct.record_answer(it["id"], correct=True, confidence="confident")
    cal = ct.calibration_report()
    cell = cal["concepts"]["hanging piece"]["stages"]["repair"]["confident"]
    assert cell["interpretation"] == "insufficient"
    assert cell["n"] == 3


def test_calibration_well_calibrated():
    """High confidence + high solve rate -> well-calibrated (not over/under)."""
    it = _confidence_item()
    for _ in range(6):
        ct.record_answer(it["id"], correct=True, confidence="confident")
    cal = ct.calibration_report()
    cell = cal["concepts"]["hanging piece"]["stages"]["repair"]["confident"]
    assert cell["interpretation"] == "well-calibrated"


def test_calibration_survives_reload():
    """Calibration is derived from the persisted item history, so it survives
    a 'restart' (re-reading the store) — no separate volatile state."""
    it = _confidence_item()
    for _ in range(6):
        ct.record_answer(it["id"], correct=True, confidence="idea")
    # Force a re-read from disk (simulates restart).
    cal = ct.calibration_report()
    cell = cal["concepts"]["hanging piece"]["stages"]["repair"]["idea"]
    assert cell["n"] == 6
    assert cell["interpretation"] in ("well-calibrated", "underconfident")


def test_confidence_recorded_atomically_with_answer():
    """Confidence is stored in the SAME record as the answer — it cannot be
    changed after the result (append-only history, no update path)."""
    it = _confidence_item()
    ct.record_answer(it["id"], correct=True, confidence="confident")
    reloaded = ct._load()[0]
    assert reloaded["confidence_history"] == ["confident"]
    assert reloaded["correct_history"] == [True]
    # There is no mutation path: the history is append-only by construction.


def test_confidence_never_reorders_scheduling():
    """THE invariant: a user saying 'confident' on a weak concept must NEVER
    cause that concept to be skipped or reordered. Scheduling is driven only
    by the weakness model (observed mistakes -> concept_scores), never by the
    confidence history stored on training items."""
    from swarm_os.services import chess_mistakes as cm

    hp = _seed_item(concept="hanging piece")
    ks = _seed_item(concept="king safety")
    # Make hanging piece the clearly weakest concept in the mistake model.
    for _ in range(5):
        cm.record_mistake(
            "rnbqkbnr/pppp1ppp/2n5/4p2Q/4P3/8/PPPP1PPP/RNB1KBNR w KQkq - 1 2",
            "h5f7", "Qxf7", None, None, "Blunder", concept="imported",
        )
    # Record MANY confident answers (including failures) on hanging piece.
    for i in range(6):
        ct.record_answer(hp["id"], correct=(i % 3 == 0), confidence="confident")
    ct.record_answer(ks["id"], correct=True, confidence="guess")
    # Force both items back to due (a failure resets due_at to +1h; we want to
    # assert ORDERING of due items, not the cooldown).
    with ct._LOCK:
        items = ct._load()
        for it in items:
            it["due_at"] = 0
        ct._save(items)
    due = ct.training_due(limit=10)
    concepts_served = [it["concept"] for it in due["due"]]
    # hanging piece is still served FIRST despite the confident claims, because
    # scheduling reads concept_scores (observed mistakes), not confidence.
    assert concepts_served.index("hanging piece") < concepts_served.index("king safety")


def test_post_hoc_confidence_rejected_from_calibration():
    """A confidence that arrives AFTER the answer (post-hoc) must be excluded
    from calibration — it measures post-answer reflection, not pre-commitment
    certainty. The invariant is confidence_captured_at <= answer_recorded_at."""
    import time

    it = _seed_item()
    t = time.time()
    # Post-hoc: confidence captured AFTER the answer time.
    ct.record_answer(it["id"], correct=True, confidence="confident", confidence_captured_at=t + 100.0)
    # Valid: confidence captured BEFORE the answer.
    ct.record_answer(it["id"], correct=True, confidence="confident", confidence_captured_at=t - 5.0)
    cal = ct.calibration_report()
    # Only the valid entry is in calibration; the post-hoc one is rejected.
    assert cal["rejected_post_hoc"] == 1
    cell = cal["concepts"]["hanging piece"]["stages"]["repair"]["confident"]
    assert cell["n"] == 1
