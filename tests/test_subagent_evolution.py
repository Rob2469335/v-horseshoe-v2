"""Evolvable config for `mutable: true` subagents.

Covers the safety contract: opt-in flag, mutable-only, staged-not-applied,
human-gated promote, reversibly back-upped, audited through the shared
AGENTS.md writer — and provable inertness for `mutable: false`.
"""

from __future__ import annotations

import json
import random
import textwrap

import pytest

import runtime_v2.services.subagent_registry as reg
import runtime_v2.services.subagent_evolution as se
import swarm_os.services.watch_loop as wl

_TUNER = """\
---
name: tuner
description: A tunable helper.
tools: filesystem, final
model: robs4b
mutable: true
budget: 4096
---
Tuner body line.
"""

_TUNER_STATIC = """\
---
name: static
description: Not tunable.
tools: filesystem, final
mutable: false
---
Static body line.
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / ".rob" / "agents"
    monkeypatch.setattr(reg, "_AGENTS_ROOT", str(root))
    monkeypatch.setattr(reg, "_CACHE", (0.0, []))
    monkeypatch.setattr(se, "STAGED_DIR", tmp_path / "staged")
    monkeypatch.setattr(se, "_agents_root", lambda: root)
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("## Self-Healing & Self-Learning Fixes\n", encoding="utf-8")
    monkeypatch.setattr(wl, "_AGENTS_MD", agents_md)
    monkeypatch.setattr(
        wl, "_AUDIT_FILE", tmp_path / "data" / "events" / "auto_repairs.jsonl"
    )
    # Hermetic score: a real outcome signal exists unless a test overrides it.
    monkeypatch.setattr(se, "score", lambda g: 0.5)
    monkeypatch.delenv("SWARM_SUBAGENT_EVOLUTION", raising=False)
    return root


def _write(root, filename: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(textwrap.dedent(content), encoding="utf-8")


# --------------------------------------------------------------------------
# opt-in + mutable-only
# --------------------------------------------------------------------------


def test_propose_is_disabled_by_default(env):
    _write(env, "tuner.md", _TUNER)
    assert se.propose("tuner") is None
    assert se.list_staged() == []


def test_propose_ignores_immutable_agents(env, monkeypatch):
    monkeypatch.setenv("SWARM_SUBAGENT_EVOLUTION", "1")
    _write(env, "static.md", _TUNER_STATIC)
    assert se.propose("static") is None
    assert se.list_staged() == []


def test_mutable_subagents_only_lists_flagged(env):
    _write(env, "tuner.md", _TUNER)
    _write(env, "static.md", _TUNER_STATIC)
    assert [s["name"] for s in se.mutable_subagents()] == ["tuner"]


# --------------------------------------------------------------------------
# mutation
# --------------------------------------------------------------------------


def test_mutate_changes_exactly_one_gene_and_does_not_mutate_input():
    g = {"tools": ["filesystem", "final"], "model": "robs4b", "budget": 4096}
    original = json.loads(json.dumps(g))
    m = se.mutate(g, random.Random(1))
    diff = [k for k in g if g[k] != m[k]]
    assert len(diff) == 1, f"expected exactly one change, got {diff}"
    assert g == original  # input untouched (deep copy)


# --------------------------------------------------------------------------
# stage (never touches the file)
# --------------------------------------------------------------------------


def test_propose_stages_and_leaves_md_untouched(env, monkeypatch):
    monkeypatch.setenv("SWARM_SUBAGENT_EVOLUTION", "1")
    _write(env, "tuner.md", _TUNER)
    before = (env / "tuner.md").read_text(encoding="utf-8")
    rec = se.propose("tuner", random.Random(0))
    assert rec is not None
    assert rec["agent"] == "tuner"
    assert se.list_staged("tuner"), "a proposal must be staged"
    assert (env / "tuner.md").read_text(encoding="utf-8") == before  # untouched


# --------------------------------------------------------------------------
# human-gated promote + audit + rollback
# --------------------------------------------------------------------------


def test_promote_rewrites_frontmatter_audits_and_backs_up(env, monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_SUBAGENT_EVOLUTION", "1")
    _write(env, "tuner.md", _TUNER)
    rec = se.propose("tuner", random.Random(3))
    assert rec is not None

    result = se.promote("tuner")
    assert result["ok"] is True

    # frontmatter updated to the candidate; body preserved
    text = (env / "tuner.md").read_text(encoding="utf-8")
    assert "Tuner body line." in text
    reloaded = reg.get_subagent("tuner")
    assert reloaded["budget"] == rec["candidate"]["budget"]
    assert reloaded["tools"] == rec["candidate"]["tools"]

    # reversible backup exists
    assert (env / "tuner.md.bak").exists()

    # audited through the shared writer (AGENTS.md line + jsonl entry)
    agents_md = tmp_path / "AGENTS.md"
    assert "[SUBAGENT-CONFIG]" in agents_md.read_text(encoding="utf-8")
    audit = tmp_path / "data" / "events" / "auto_repairs.jsonl"
    assert audit.exists()
    assert json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])["type"] == (
        "SUBAGENT-CONFIG"
    )


def test_rollback_restores_the_original(env, monkeypatch):
    monkeypatch.setenv("SWARM_SUBAGENT_EVOLUTION", "1")
    _write(env, "tuner.md", _TUNER)
    original = (env / "tuner.md").read_text(encoding="utf-8")
    se.propose("tuner", random.Random(5))
    se.promote("tuner")
    assert (env / "tuner.md").read_text(encoding="utf-8") != original
    assert se.rollback("tuner")["ok"] is True
    assert (env / "tuner.md").read_text(encoding="utf-8") == original


def test_repeated_promote_keeps_the_original_for_rollback(env, monkeypatch):
    """A second promotion must not overwrite the backup — one rollback has to
    restore the pre-evolution ORIGINAL, not the intermediate state."""
    monkeypatch.setenv("SWARM_SUBAGENT_EVOLUTION", "1")
    _write(env, "tuner.md", _TUNER)
    original = (env / "tuner.md").read_text(encoding="utf-8")
    se.propose("tuner", random.Random(1))
    assert se.promote("tuner")["ok"] is True
    se.propose("tuner", random.Random(2))
    assert se.promote("tuner")["ok"] is True
    assert (env / "tuner.md").read_text(encoding="utf-8") != original
    assert se.rollback("tuner")["ok"] is True
    assert (env / "tuner.md").read_text(encoding="utf-8") == original


def test_promote_refuses_immutable_and_unstaged(env):
    _write(env, "static.md", _TUNER_STATIC)
    assert se.promote("static")["ok"] is False
    _write(env, "tuner.md", _TUNER)
    assert se.promote("tuner")["ok"] is False  # nothing staged


def test_list_staged_ignores_non_record_json(env):
    """A stray valid-JSON non-record in the staged dir must not crash the
    surface (it used to raise AttributeError on rec.get)."""
    se.STAGED_DIR.mkdir(parents=True, exist_ok=True)
    (se.STAGED_DIR / "junk.json").write_text("[]", encoding="utf-8")
    assert se.list_staged() == []


def test_shadowed_builtin_name_is_not_evolvable(env, monkeypatch):
    """A file that shadows a built-in is never used by the runtime, so it must
    not be proposed (else the audit claims a built-in 'evolved' while its
    behavior is unchanged)."""
    monkeypatch.setenv("SWARM_SUBAGENT_EVOLUTION", "1")
    _write(
        env,
        "executor.md",
        "---\nname: executor\ndescription: shadow\nmutable: true\n"
        "tools: filesystem\nbudget: 4096\n---\nbody\n",
    )
    assert se.mutable_subagents() == []
    assert se.propose("executor") is None
    assert se.list_staged() == []


# --------------------------------------------------------------------------
# evidence-backed gates
# --------------------------------------------------------------------------


def test_propose_requires_a_real_outcome_signal(env, monkeypatch):
    """Anti-fabrication: no mutation without a real signal."""
    monkeypatch.setenv("SWARM_SUBAGENT_EVOLUTION", "1")
    monkeypatch.setattr(se, "score", lambda g: 0.0)
    _write(env, "tuner.md", _TUNER)
    assert se.propose("tuner") is None
    assert se.list_staged() == []


def test_propose_skips_a_worse_candidate(env, monkeypatch):
    """Regression-risk gate: a candidate measuring worse is not staged."""
    monkeypatch.setenv("SWARM_SUBAGENT_EVOLUTION", "1")
    monkeypatch.setattr(
        se,
        "mutate",
        lambda g, rng=None: {**g, "budget": 5120},
    )
    monkeypatch.setattr(se, "score", lambda g: 0.5 if g["budget"] == 4096 else 0.2)
    _write(env, "tuner.md", _TUNER)
    assert se.propose("tuner") is None
    assert se.list_staged() == []


def test_promote_enforces_acceptance_invariants(env, monkeypatch, tmp_path):
    """A staged candidate with an unknown tool is refused at promotion."""
    monkeypatch.setenv("SWARM_SUBAGENT_EVOLUTION", "1")
    monkeypatch.setattr(
        se,
        "mutate",
        lambda g, rng=None: {**g, "tools": ["filesystem", "not-a-real-tool"]},
    )
    _write(env, "tuner.md", _TUNER)
    assert se.propose("tuner") is not None  # staged without validating
    res = se.promote("tuner")
    assert res["ok"] is False
    assert "acceptance gate failed" in res["reason"]
    # the live file was NOT touched
    assert "not-a-real-tool" not in (env / "tuner.md").read_text(encoding="utf-8")


def test_promote_records_reviewer_and_reject_discards(env, monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_SUBAGENT_EVOLUTION", "1")
    _write(env, "tuner.md", _TUNER)
    se.propose("tuner", random.Random(7))
    res = se.promote("tuner", reviewer="alice")
    assert res["ok"] is True
    audit = tmp_path / "data" / "events" / "auto_repairs.jsonl"
    assert (
        json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])["reviewer"]
        == "alice"
    )

    # reject on a fresh proposal discards it + audits
    se.propose("tuner", random.Random(8))
    assert se.list_staged("tuner")
    rej = se.reject("tuner", reviewer="bob")
    assert rej["ok"] is True and rej["removed"] >= 1
    assert se.list_staged("tuner") == []
    assert (
        json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])["type"]
        == "SUBAGENT-CONFIG-REJECTED"
    )
