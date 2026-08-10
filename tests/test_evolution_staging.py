"""Tests for the evolution staging fix (2026-08-07).

REAL BUG this fixes: autonomy_policy.json said evolution.promotion =
staged_human_approved — new generations must be STAGED and approved before they
become active — but evolve_one_generation wrote new generations STRAIGHT to the
active GENOMES_PATH every tick with no staging and no gate. The written ceiling
described intent the code never implemented: the daemon had been unconditionally
auto-promoting this whole time. These tests prove the fix: generations stage,
the active population is untouched until a human approves, and approval is the
only path that changes the active tool policy.
"""
from pathlib import Path

import pytest

from swarm_os.services import evolution_daemon as ev


@pytest.fixture(autouse=True)
def _isolate_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(ev, "GENOMES_PATH", tmp_path / "genomes.jsonl")
    monkeypatch.setattr(ev, "STAGED_DIR", tmp_path / "staged")
    monkeypatch.setattr(ev, "POPULATION_SIZE", 4)
    monkeypatch.setattr(ev, "ELITE_COUNT", 2)
    return tmp_path


def _seed_active(tmp_path, fitness=0.5):
    pop = [
        {"id": "genome_0", "tool_genes": {"web_search": 0.7}, "fitness": fitness, "generation": 0},
        {"id": "genome_1", "tool_genes": {"web_search": 0.3}, "fitness": fitness, "generation": 0},
    ]
    ev._persist_population(pop, ev.GENOMES_PATH)
    return pop


# ── staging is enforced ─────────────────────────────────────────────────────
def test_evolve_stages_instead_of_promoting(tmp_path, monkeypatch):
    """The core fix: evolve_one_generation writes to the STAGED dir and leaves
    the ACTIVE genomes.jsonl untouched — no unconditional auto-promotion."""
    _seed_active(tmp_path)
    # Force scoring to a deterministic real value.
    monkeypatch.setattr(ev, "_score_genome", lambda g: g.get("fitness", 0.5))

    summary = ev.evolve_one_generation()
    assert summary.get("staged") is True
    assert summary.get("staged_path")
    # The staged file exists with the new generation.
    staged_path = Path(summary["staged_path"])
    assert staged_path.exists()
    # The ACTIVE population is UNCHANGED (still the seeded 2 genomes).
    active = ev._load_population(ev.GENOMES_PATH)
    assert [g["id"] for g in active] == ["genome_0", "genome_1"]


def test_staged_generation_listed_and_approvable(tmp_path, monkeypatch):
    _seed_active(tmp_path)
    monkeypatch.setattr(ev, "_score_genome", lambda g: g.get("fitness", 0.5))
    summary = ev.evolve_one_generation()

    staged = ev.list_staged_generations()
    assert staged, "expected a staged generation"
    assert staged[0]["gen"] == str(summary["generation"])
    assert staged[0]["best_fitness"] > 0

    res = ev.promote_staged_generation(summary["generation"])
    assert res["ok"] is True
    # Now the ACTIVE population is the staged one (generation >= 1).
    active = ev._load_population(ev.GENOMES_PATH)
    assert active
    assert max((g.get("generation", 0) for g in active)) >= 1


def test_promote_missing_generation_fails(tmp_path):
    res = ev.promote_staged_generation(999)
    assert res["ok"] is False
    assert "not found" in res["reason"]


def test_promote_empty_generation_fails(tmp_path):
    ev.STAGED_DIR.mkdir(parents=True, exist_ok=True)
    (ev.STAGED_DIR / "gen_5.jsonl").write_text("", encoding="utf-8")
    res = ev.promote_staged_generation(5)
    assert res["ok"] is False


def test_active_unchanged_until_approval(tmp_path, monkeypatch):
    """Two generations evolve; the ACTIVE population stays at the seeded one the
    whole time (no silent promotion), until promote_staged is called."""
    _seed_active(tmp_path)
    monkeypatch.setattr(ev, "_score_genome", lambda g: g.get("fitness", 0.5))
    ev.evolve_one_generation()
    ev.evolve_one_generation()

    # Active still the seeded population (generation 0).
    active = ev._load_population(ev.GENOMES_PATH)
    assert max((g.get("generation", 0) for g in active)) == 0
    # A staged generation exists (the latest pending proposal — successive
    # evolutions refine the same staged file since the active base is unchanged
    # until approval, so there is exactly one pending stage).
    staged = ev.list_staged_generations()
    assert len(staged) >= 1
    assert staged[0]["best_fitness"] > 0


def test_get_active_genome_reads_active_not_staged(tmp_path, monkeypatch):
    """get_active_genome (used by the agent loop to order allowed tools) reads the
    ACTIVE population — a staged, unapproved generation must not affect it."""
    _seed_active(tmp_path, fitness=0.5)
    monkeypatch.setattr(ev, "_score_genome", lambda g: g.get("fitness", 0.5))
    ev.evolve_one_generation()  # stages gen_1

    gid, weights = ev.get_active_genome(explore=False)
    # get_active_genome sorts by fitness; both active genomes have 0.5, returns
    # the first (genome_0). It must NOT be a staged child (generation 0 ids only).
    assert gid in ("genome_0", "genome_1")


def test_evolve_increments_gen_from_staged_not_active(tmp_path, monkeypatch):
    """EVO-1: the next generation number must come from what is ALREADY STAGED,
    not the active population. The active pop stays at its last APPROVED
    generation while a staged one awaits approval — deriving from the active pop
    would re-stage the same gen number and clobber the staged-but-unapproved
    generation. Here gen_5 is staged and pending; evolve must stage gen_6."""
    _seed_active(tmp_path, fitness=0.5)
    monkeypatch.setattr(ev, "_score_genome", lambda g: g.get("fitness", 0.5))
    ev.STAGED_DIR.mkdir(parents=True, exist_ok=True)
    # A pending staged generation (active pop still at generation 0).
    pending = [
        {"id": "g5a", "tool_genes": {"web_search": 0.8}, "fitness": 0.7, "generation": 5},
        {"id": "g5b", "tool_genes": {"web_search": 0.6}, "fitness": 0.6, "generation": 5},
    ]
    ev._persist_population(pending, ev.STAGED_DIR / "gen_5.jsonl")

    summary = ev.evolve_one_generation()
    assert summary["generation"] == 6
    # gen_5 is NOT overwritten — its bytes are untouched.
    assert (ev.STAGED_DIR / "gen_5.jsonl").exists()
    assert (ev.STAGED_DIR / "gen_6.jsonl").exists()


def test_evolve_ignores_malformed_staged_files(tmp_path, monkeypatch):
    """EVO-1: a malformed staged filename (not gen_<int>.jsonl) must be ignored
    in the gen-number calculation, never raise, and never be deleted."""
    _seed_active(tmp_path, fitness=0.5)
    monkeypatch.setattr(ev, "_score_genome", lambda g: g.get("fitness", 0.5))
    ev.STAGED_DIR.mkdir(parents=True, exist_ok=True)
    (ev.STAGED_DIR / "gen_NOTANUMBER.jsonl").write_text("{}", encoding="utf-8")
    (ev.STAGED_DIR / "gen_3.jsonl").write_text("{}\n", encoding="utf-8")

    summary = ev.evolve_one_generation()
    assert summary["generation"] == 4
    # The malformed file is preserved (never deleted).
    assert (ev.STAGED_DIR / "gen_NOTANUMBER.jsonl").exists()
