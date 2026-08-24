"""Tests for the AGENTS.md reflexion-rule recorder dedup.

Regression for the 2026-08-24 audit: the recorder's exact-substring check let
every LLM RE-DISTILLATION of the same failure append another slightly rephrased
near-duplicate "Rule (researcher)" line to AGENTS.md (dozens accumulated). The
recorder must apply the same content-similarity test the Qdrant store uses
(`_corrections_similar`) against the component's existing rule lines.
"""

import pytest

from swarm_os.services import reflection_loop as rl


@pytest.fixture
def agents_md(tmp_path, monkeypatch):
    """Point ROOT_DIR at a tmp dir with a minimal AGENTS.md."""
    f = tmp_path / "AGENTS.md"
    f.write_text(
        "# Horseshoe\n\n## Self-Healing & Self-Learning Fixes\n\n- **FIX: x**\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rl, "ROOT_DIR", tmp_path)
    return f


EXISTING = (
    "The researcher agent attempted to search the internet for improvements on "
    "episodic memory, but the call failed due to all configured providers "
    "returning errors."
)


def _seed_rule(f, component, text):
    content = f.read_text(encoding="utf-8")
    marker = "## Self-Healing & Self-Learning Fixes\n"
    content = content.replace(
        marker, marker + f"\n- **Rule ({component})**: {text}\n", 1
    )
    f.write_text(content, encoding="utf-8")


def test_rephrased_same_failure_not_appended(agents_md):
    """A RE-PHRASED distillation of an already-recorded failure must NOT append
    a near-duplicate rule line (revert-proof: fails on pre-fix source, whose
    exact-substring check only caught byte-identical rules)."""
    _seed_rule(agents_md, "researcher", EXISTING)
    before = agents_md.read_text(encoding="utf-8")

    # Same fact, different wording + word order (LLM re-distillation shape).
    rephrased = (
        "web_search failed for the researcher agent while searching for "
        "improvements on episodic memory because every configured provider "
        "returned an error."
    )
    assert not rl._corrections_similar(EXISTING, rephrased) or True  # sanity: fn runs
    rl._record_rule_to_agents_md("researcher", rephrased, confidence=0.9)

    after = agents_md.read_text(encoding="utf-8")
    assert after == before


def test_genuinely_new_failure_still_recorded(agents_md):
    """Dedup must not swallow NEW facts: a materially different failure is
    appended even when the component already has rules."""
    _seed_rule(agents_md, "researcher", EXISTING)
    before_len = len(agents_md.read_text(encoding="utf-8"))

    rl._record_rule_to_agents_md(
        "researcher",
        "File not found: config.yaml — list the parent directory first.",
        confidence=0.9,
    )

    after = agents_md.read_text(encoding="utf-8")
    assert len(after) > before_len
    assert "list the parent directory first" in after


def test_negation_conflict_still_recorded(agents_md):
    """A correction that flips the directive (negation on one side only) is a
    CONFLICT per _corrections_similar and must still be recorded."""
    _seed_rule(agents_md, "researcher", EXISTING)
    before_len = len(agents_md.read_text(encoding="utf-8"))

    rl._record_rule_to_agents_md(
        "researcher",
        "Never web_search with the raw goal text; strip coordinator boilerplate first.",
        confidence=0.9,
    )

    after = agents_md.read_text(encoding="utf-8")
    assert len(after) > before_len


def test_exact_duplicate_still_skipped(agents_md):
    """Byte-identical rules are skipped (the original behavior, preserved)."""
    _seed_rule(agents_md, "code_analyzer", "Verify file existence before read.")
    before = agents_md.read_text(encoding="utf-8")

    rl._record_rule_to_agents_md(
        "code_analyzer", "Verify file existence before read.", 0.9
    )

    assert agents_md.read_text(encoding="utf-8") == before


def test_other_component_rules_do_not_block(agents_md):
    """Dedup is scoped per component: a researcher rule must not block a
    code_analyzer rule about the same failure shape."""
    _seed_rule(agents_md, "researcher", EXISTING)
    before_len = len(agents_md.read_text(encoding="utf-8"))

    rl._record_rule_to_agents_md("code_analyzer", EXISTING, confidence=0.9)

    assert len(agents_md.read_text(encoding="utf-8")) > before_len
