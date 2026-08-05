"""Tests for the self-learning critic persistence (Fix 4).

The critic journal was write-only: MetaCritic weights reset to defaults on every
restart, so the 'evolution' (weight adjustments from prediction error) was lost.
These verify the journal can be read back and used to seed weights.
"""
from __future__ import annotations
import json


def test_critic_journal_roundtrip(tmp_path):
    from runtime_v2.services.learning.critic_journal import CriticJournal

    path = tmp_path / "critic_journal.jsonl"
    journal = CriticJournal(str(path))
    journal.log({"predicted": 0.5, "actual": True, "score": 0.9, "weights": {}})
    journal.log({"predicted": 0.5, "actual": False, "score": 0.2, "weights": {}})

    entries = journal.load()
    assert len(entries) == 2
    assert entries[0]["actual"] is True
    assert entries[1]["actual"] is False


def test_critic_journal_tolerates_corrupt_lines(tmp_path):
    from runtime_v2.services.learning.critic_journal import CriticJournal

    path = tmp_path / "critic_journal.jsonl"
    path.write_text('{"actual": true}\nNOT JSON\n{"actual": false}\n', encoding="utf-8")
    journal = CriticJournal(str(path))
    entries = journal.load()
    assert len(entries) == 2  # corrupt line skipped, not fatal


def test_meta_critic_seeds_weights_from_history(tmp_path):
    """Failures should raise failure_penalty / lower success_weight in the seeded
    critic — proving the journal history actually steers the critic."""
    from runtime_v2.services.learning.critic_journal import CriticJournal
    from runtime_v2.services.learning.meta_critic import MetaCritic

    path = tmp_path / "critic_journal.jsonl"
    journal = CriticJournal(str(path))
    for _ in range(50):
        journal.log({"predicted": 0.5, "actual": False, "score": 0.3, "weights": {}})

    critic = MetaCritic.from_history(journal.load())
    assert critic.weights["failure_penalty"] > 0.4
    assert critic.weights["success_weight"] < 0.8


def test_evolving_critic_seeds_from_journal_on_init(tmp_path, monkeypatch):
    """EvolvingCritic must construct a history-seeded critic (not fresh defaults)
    when a journal exists — the persistence fix."""
    from runtime_v2.services.learning.critic_journal import CriticJournal
    from runtime_v2.services.learning.evolving_critic import EvolvingCritic

    journal_path = tmp_path / "critic_journal.jsonl"
    journal = CriticJournal(str(journal_path))
    for _ in range(50):
        journal.log({"predicted": 0.5, "actual": False, "score": 0.3, "weights": {}})

    monkeypatch.setattr(
        "runtime_v2.services.learning.evolving_critic.CriticJournal",
        lambda *a, **k: CriticJournal(str(journal_path)),
    )
    ec = EvolvingCritic()
    assert ec.critic.weights["failure_penalty"] > 0.4
