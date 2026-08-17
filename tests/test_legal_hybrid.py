"""Tests for hybrid lexical+dense retrieval with Reciprocal Rank Fusion
(swarm_os/services/legal/hybrid_search.py).

The research-backed claim (AusLaw 2412.06272 / Anthropic Contextual Retrieval):
legal queries are phrase/citation-exact where dense embeddings miss exact
tokens, so lexical + dense fused with RRF beats either alone. These tests pin
the deterministic lexical leg, the RRF fusion, and the exact-citation recall.
"""

from __future__ import annotations

from swarm_os.services.legal.hybrid_search import (
    bm25_score,
    rrf_fuse,
    hybrid_fuse,
    lexical_rank,
    tokenize,
)


def test_tokenize_splits_legal_shapes():
    assert "235" in tokenize("N.Y. RPA Law § 235-b")
    assert "576" in tokenize("576 U.S. 644")
    assert tokenize("576 U.S. 644") == ["576", "u", "s", "644"]


def test_bm25_score_ranks_relevant_doc_higher():
    q = tokenize("tenant notice deposit return")
    relevant = tokenize(
        "the tenant must receive notice before the landlord keeps the deposit"
    )
    irrelevant = tokenize("the contractor must finish the roof by Friday")
    assert bm25_score(q, relevant) > bm25_score(q, irrelevant)


def test_bm25_score_zero_when_no_overlap():
    assert bm25_score(["foo", "bar"], ["baz"]) == 0.0
    assert bm25_score([], ["anything"]) == 0.0


def test_rrf_fuse_merges_two_lists_by_rank():
    dense = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    lexical = [{"id": "b"}, {"id": "c"}, {"id": "d"}]
    fused = rrf_fuse([dense, lexical])
    ids = [it["id"] for it in fused]
    # "b" appears rank 1 in lexical and rank 2 in dense -> highest fused.
    assert ids[0] == "b"
    assert "d" in ids
    # Every fused item carries its RRF score.
    assert all("rrf_score" in it for it in fused)


def test_rrf_fuse_handles_disjoint_and_shared():
    fused = rrf_fuse([[{"id": "x"}, {"id": "y"}], [{"id": "z"}, {"id": "y"}]])
    ids = [it["id"] for it in fused]
    assert ids[0] == "y", "shared doc must rank first (1/(60+1)+1/(60+1))"
    assert set(ids) == {"x", "y", "z"}


def test_hybrid_fuse_weights_lexical_higher():
    dense = [{"id": "a"}, {"id": "b"}]
    lexical = [{"id": "b"}, {"id": "a"}]
    # Equal weights -> both rank the same; check the shared ordering is stable.
    fused_eq = hybrid_fuse(dense, lexical)
    assert fused_eq[0]["id"] in ("a", "b")
    # Lexical-weighted (3x) -> b (lexical #1) must win over a.
    fused_lex = hybrid_fuse(dense, lexical, dense_weight=1.0, lexical_weight=3.0)
    assert fused_lex[0]["id"] == "b"


def test_lexical_rank_exact_citation_recall():
    """The core legal-IR claim: an EXACT citation query must surface the
    document containing that citation at the top even when dense might bury it
    (the "576 U.S. 644" exact-token case)."""
    pool = [
        {
            "id": 1,
            "content": "The rule from Obergefell v. Hodges, 576 U.S. 644 (2015) controls.",
        },
        {
            "id": 2,
            "content": "A discussion of marriage equality generally, no citation.",
        },
    ]
    ranked = lexical_rank("576 U.S. 644", pool)
    assert ranked[0]["id"] == 1, "the exact citation doc must rank first lexically"
    assert ranked[0]["lexical_score"] > ranked[1]["lexical_score"]


def test_lexical_rank_empty_safe():
    assert lexical_rank("", [{"id": 1, "content": "x"}]) == []
    assert lexical_rank("q", []) == []
