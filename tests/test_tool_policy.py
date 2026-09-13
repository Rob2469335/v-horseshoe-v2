"""Contextual tool policy: per-shape learning + adaptive shortlist (BoR)."""

from __future__ import annotations

import runtime_v2.services.tool_policy as tp


def test_shape_of_classifies_common_tasks():
    assert tp.shape_of("Use sandbox_repl to compute 17 * 23") == "math"
    assert tp.shape_of("Read runtime_v2/api/_agent_config.py") == "code"
    assert tp.shape_of("Use the git tool to report the current branch") == "git"
    assert tp.shape_of("report the operating system platform") == "system"
    assert tp.shape_of("hello there, who are you?") == "other"


def test_record_and_weights(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "OBSERVATIONS", tmp_path / "obs.jsonl")
    tp.record_observation("read a file", ["filesystem"], True)
    tp.record_observation("read a file", ["filesystem"], True)
    tp.record_observation("read a file", ["web_search"], False)
    w = tp.tool_weights("code")
    assert w["filesystem"] > w["web_search"]
    # shape filter excludes non-matching shapes
    assert tp.tool_weights("math") == {}


def test_rank_prefers_learned_success(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "OBSERVATIONS", tmp_path / "obs.jsonl")
    monkeypatch.setattr(tp, "_fitness_genes", lambda: {})
    for _ in range(3):
        tp.record_observation("read a file", ["filesystem"], True)
    for _ in range(3):
        tp.record_observation("read a file", ["web_search"], False)
    ranked = tp.rank("read a file", ["web_search", "filesystem", "final"])
    assert ranked.index("filesystem") < ranked.index("web_search")


def test_shortlist_keeps_core_and_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "OBSERVATIONS", tmp_path / "obs.jsonl")
    monkeypatch.setattr(tp, "_fitness_genes", lambda: {})
    tools = [
        "final",
        "filesystem",
        "sandbox_repl",
        "web_search",
        "delegate",
        "web_fetch",
        "lsp",
        "mcp",
        "email",
        "playwright",
        "todo",
        "remember",
        "system",
        "screen",
        "semantic_search",
    ]
    out = tp.shortlist("read a file and fix a bug", tools, k=12)
    assert len(out) == 12
    for core in ("final", "filesystem", "sandbox_repl", "web_search", "delegate"):
        assert core in out  # never dropped


def test_shortlist_noop_when_small(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "OBSERVATIONS", tmp_path / "obs.jsonl")
    monkeypatch.setattr(tp, "_fitness_genes", lambda: {})
    tools = ["final", "filesystem", "sandbox_repl"]
    assert set(tp.shortlist("x", tools, k=12)) == set(tools)


def test_fail_open_on_missing_store(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "OBSERVATIONS", tmp_path / "nope" / "obs.jsonl")
    monkeypatch.setattr(tp, "_fitness_genes", lambda: {})
    assert tp.rank("x", ["a", "b"]) == ["a", "b"]
    assert tp.shortlist("x", ["a", "b"]) == ["a", "b"]
    assert tp.tool_weights("code") == {}


def test_enabled_flag(monkeypatch):
    monkeypatch.delenv("SWARM_TOOL_SHORTLIST", raising=False)
    assert tp.enabled() is False
    monkeypatch.setenv("SWARM_TOOL_SHORTLIST", "1")
    assert tp.enabled() is True


def test_lexical_retrieval_surfaces_the_right_tool(tmp_path, monkeypatch):
    """P2 retrieval half: with NO learned history, description match ranks the
    tool the task actually needs above unrelated tools."""
    monkeypatch.setattr(tp, "OBSERVATIONS", tmp_path / "obs.jsonl")
    monkeypatch.setattr(tp, "_fitness_genes", lambda: {})
    tools = ["sandbox_repl", "web_search", "web_fetch", "email", "lsp"]
    ranked = tp.rank("search the web for the latest python release", tools)
    assert ranked[0] in ("web_search", "web_fetch")
    assert ranked.index("web_search") < ranked.index("email")


def test_lexical_scores_are_idf_weighted(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "_fitness_genes", lambda: {})
    scores = tp.lexical_scores("read a python file", ["filesystem", "email"])
    assert scores["filesystem"] >= scores["email"]
