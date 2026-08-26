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
        {
            "id": "genome_0",
            "tool_genes": {
                "filesystem": 0.6,
                "web_search": 0.7,
                "web_fetch": 0.5,
                "sandbox_repl": 0.5,
            },
            "fitness": fitness,
            "generation": 0,
        },
        {
            "id": "genome_1",
            "tool_genes": {
                "filesystem": 0.6,
                "web_search": 0.5,
                "web_fetch": 0.5,
                "sandbox_repl": 0.5,
            },
            "fitness": fitness,
            "generation": 0,
        },
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
        {
            "id": "g5a",
            "tool_genes": {"web_search": 0.8},
            "fitness": 0.7,
            "generation": 5,
        },
        {
            "id": "g5b",
            "tool_genes": {"web_search": 0.6},
            "fitness": 0.6,
            "generation": 5,
        },
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


def test_tool_gene_floors_enforced_in_seed_and_crossover():
    """Verify that essential tool genes (filesystem, web_search, web_fetch) never drop below floors."""
    pop = ev._seed_population()
    for g in pop:
        tg = g["tool_genes"]
        assert tg["filesystem"] >= 0.50
        assert tg["web_search"] >= 0.40
        assert tg["web_fetch"] >= 0.40

    # Test extreme parent with low/missing values
    p1 = {
        "tool_genes": {"filesystem": 0.01, "web_search": 0.01, "semantic_search": 0.9}
    }
    p2 = {"tool_genes": {"filesystem": 0.05, "web_search": 0.05, "sandbox_repl": 0.8}}

    for _ in range(50):
        child = ev._crossover_mutate(p1, p2, generation=10)
        ctg = child["tool_genes"]
        assert ctg["filesystem"] >= 0.50, (
            f"filesystem fell below floor: {ctg['filesystem']}"
        )
        assert ctg["web_search"] >= 0.40, (
            f"web_search fell below floor: {ctg['web_search']}"
        )
        assert ctg["web_fetch"] >= 0.40, (
            f"web_fetch fell below floor: {ctg['web_fetch']}"
        )


# ── Automated Promotion Gating & Rollback ──────────────────────────────────
def test_auto_promote_exceeds_margin(tmp_path, monkeypatch):
    """When auto_promote=True and staged_best >= active_best + margin, promote."""
    _seed_active(tmp_path, fitness=0.5)

    # Active scores 0.5, new generation scores 0.7 (exceeds 0.03 margin)
    def mock_score(g):
        if g.get("generation", 0) == 0:
            return 0.5
        return 0.7

    monkeypatch.setattr(ev, "_score_genome", mock_score)

    summary = ev.evolve_one_generation(auto_promote=True, min_improvement=0.03)
    assert summary["promoted"] is True
    assert summary["active_best"] == 0.5
    assert summary["staged_best"] == 0.7

    active = ev._load_population(ev.GENOMES_PATH)
    assert active[0].get("generation", 0) >= 1
    # Check backup snapshot was created
    assert (ev.GENOMES_PATH.with_suffix(".jsonl.bak")).exists()


def test_auto_promote_rejects_tie_or_sub_margin(tmp_path, monkeypatch):
    """When staged_best is a tie or within margin, auto_promote does NOT promote."""
    _seed_active(tmp_path, fitness=0.5)

    # Staged best is 0.51 (improvement is 0.01, below 0.03 margin)
    def mock_score(g):
        if g.get("generation", 0) == 0:
            return 0.5
        return 0.51

    monkeypatch.setattr(ev, "_score_genome", mock_score)

    summary = ev.evolve_one_generation(auto_promote=True, min_improvement=0.03)
    assert summary["promoted"] is False
    assert "did not beat active best" in summary["promotion_reason"]

    # Active population stays at generation 0
    active = ev._load_population(ev.GENOMES_PATH)
    assert [g["id"] for g in active] == ["genome_0", "genome_1"]


def test_auto_promote_rejects_tool_floor_violation(tmp_path, monkeypatch):
    """If a staged generation violates tool floors, promotion is rejected."""
    _seed_active(tmp_path, fitness=0.5)
    ev.STAGED_DIR.mkdir(parents=True, exist_ok=True)

    # Create an invalid staged generation with low tool gene
    invalid_pop = [
        {
            "id": "bad_genome",
            "tool_genes": {"filesystem": 0.10, "web_search": 0.40, "web_fetch": 0.40},
            "fitness": 0.99,
            "generation": 1,
        }
    ]
    ev._persist_population(invalid_pop, ev.STAGED_DIR / "gen_1.jsonl")

    res = ev.promote_staged_generation(1, enforce_floors=True)
    assert res["ok"] is False
    assert "tool floor check failed" in res["reason"]


def test_rollback_promotion_restores_backup(tmp_path, monkeypatch):
    """rollback_promotion restores the active population from genomes.jsonl.bak."""
    _seed_active(tmp_path, fitness=0.5)

    def mock_score(g):
        return 0.8 if g.get("generation", 0) > 0 else 0.5

    monkeypatch.setattr(ev, "_score_genome", mock_score)

    # Promote gen 1
    summary = ev.evolve_one_generation(auto_promote=True, min_improvement=0.03)
    assert summary["promoted"] is True
    assert ev._load_population(ev.GENOMES_PATH)[0]["generation"] >= 1

    # Execute rollback
    rb = ev.rollback_promotion()
    assert rb["ok"] is True
    assert rb["action"] == "rollback_promotion"

    # Active population restored to generation 0
    restored = ev._load_population(ev.GENOMES_PATH)
    assert [g["id"] for g in restored] == ["genome_0", "genome_1"]


# ── challenge rotation (#1, 2026-08-25) ────────────────────────────────────
def test_challenge_rotation_fields_candidates_without_improvement(
    tmp_path, monkeypatch
):
    """Children that have never been fielded have no exact fitness record, so
    they can only score the aggregate fallback (agg*0.80 = 0.76) — below an
    elite's decayed exact score (0.95*0.85 = 0.8075). Under promote-only-if-
    better NO child could ever be promoted BY DEFINITION (~320 generations
    bred into the void, gen 884 -> 1206). Challenge rotation promotes the
    staged generation every SWARM_EVOLUTION_CHALLENGE_EVERY generations even
    when it doesn't beat the active best — fielding candidates so epsilon-
    greedy can feed them real outcomes. Revert-proof: pre-fix code ignores
    the env entirely and leaves promoted=False."""
    _seed_active(tmp_path)
    monkeypatch.setattr(ev, "_score_genome", lambda g: g.get("fitness", 0.5))
    monkeypatch.setenv("SWARM_EVOLUTION_CHALLENGE_EVERY", "1")

    summary = ev.evolve_one_generation()

    assert summary.get("promoted") is True
    assert "Challenge rotation" in summary.get("promotion_reason", "")
    # active population now CONTAINS the staged children (they were fielded)
    active = ev._load_population(ev.GENOMES_PATH)
    assert len(active) == 4  # elites + children per POPULATION_SIZE fixture


def test_no_rotation_when_improve_gate_or_env_off(tmp_path, monkeypatch):
    """Default policy holds: without the env opt-in and without beating the
    improvement margin, the generation stays STAGED (staged_human_approved)."""
    _seed_active(tmp_path)
    monkeypatch.setattr(ev, "_score_genome", lambda g: g.get("fitness", 0.5))
    monkeypatch.delenv("SWARM_EVOLUTION_CHALLENGE_EVERY", raising=False)

    summary = ev.evolve_one_generation()
    assert summary.get("promoted") is False
    assert summary.get("staged") is True

    # explicit auto_promote wins over rotation; a failing improvement gate
    # must NOT fall through to rotation
    monkeypatch.setenv("SWARM_EVOLUTION_CHALLENGE_EVERY", "1")
    summary2 = ev.evolve_one_generation(auto_promote=True)
    assert summary2.get("promoted") is False
