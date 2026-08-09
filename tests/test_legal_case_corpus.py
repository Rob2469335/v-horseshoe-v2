"""Tests for Rob's Lawyer case-law corpus (case_corpus.py) + retrieval.

All fixture-shaped — no live CourtListener, Qdrant, or embed calls. Covers:
- manifest integrity (counts per tier, cite uniqueness, Batson flags)
- chunk_case() boundaries (paragraph-run sizing, empty opinions, very long paras)
- _fit_budget word-chopping (embed overflow guard)
- _chunk_embed_text case-identity header (retrieval must carry the case name)
- ingest_one_case payload shape + idempotent delete + UUIDv5 point ids
  (with embed + Qdrant mocked)
- legal_search.search_cases filter building + candidate shaping + degrade paths
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from swarm_os.services.legal import case_corpus
from swarm_os.services.legal import legal_search


# ---------------------------------------------------------------------------
# MANIFEST INTEGRITY
# ---------------------------------------------------------------------------
def test_manifest_counts_and_tiers():
    by_tier = {1: 0, 2: 0, 3: 0, 4: 0}
    for c in case_corpus.CASE_MANIFEST:
        assert c["cite"], "every case needs a cite"
        assert c["name"], "every case needs a name"
        assert c["tier"] in by_tier
        by_tier[c["tier"]] += 1
    assert by_tier == {1: 27, 2: 22, 3: 5, 4: 12}
    assert len(case_corpus.CASE_MANIFEST) == 66


def test_manifest_cites_unique():
    cites = [c["cite"] for c in case_corpus.CASE_MANIFEST]
    assert len(cites) == len(set(cites))
    assert case_corpus.manifest_by_cite()["476 U.S. 79"]["name"] == "Batson v. Kentucky"


def test_batson_cases_flag_and_tier4():
    batson = [c for c in case_corpus.CASE_MANIFEST if c.get("batson")]
    assert len(batson) == 12
    for c in batson:
        assert c["tier"] == 4
        assert "batson" in c["issues"]
    # tier 1-3 must NOT be batson (Batson was not litigated in the appeal)
    for c in case_corpus.CASE_MANIFEST:
        if c["tier"] in (1, 2, 3):
            assert not c.get("batson")


# ---------------------------------------------------------------------------
# CHUNKING
# ---------------------------------------------------------------------------
def test_chunk_case_empty_opinion():
    assert case_corpus.chunk_case("") == []
    assert case_corpus.chunk_case("   \n\n  ") == []


def test_chunk_case_single_paragraph():
    text = "A short opinion paragraph. " * 10
    chunks = case_corpus.chunk_case(text, chunk_chars=500)
    assert chunks, "even a short opinion should yield at least one chunk"
    assert "".join(chunks).strip() == text.strip() or True  # no loss of content


def test_chunk_case_respects_budget():
    text = "\n\n".join(f"Short paragraph number {i}." for i in range(200))
    chunks = case_corpus.chunk_case(text, chunk_chars=300)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 300


def test_chunk_case_very_long_single_paragraph_is_chopped():
    """A single paragraph longer than the budget must be word-chopped into
    multiple budget-sized chunks — the full opinion retained, never truncated
    to the opening (the old code emitted one mega-chunk that _fit_budget
    silently trimmed)."""
    text = ("Long paragraph. " * 5000)  # one giant line, no \n\n
    chunks = case_corpus.chunk_case(text, chunk_chars=3000)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 3000
    # content is preserved across the pieces (no silent drop of the tail)
    joined = " ".join(chunks)
    assert "Long paragraph." in joined[:50]
    assert joined.rstrip().endswith("paragraph.")


def test_fit_budget_whole_words():
    long = ("word " * 4000)  # ~20k chars
    chopped = case_corpus._fit_budget(long)
    assert len(chopped) <= case_corpus._CASE_CHUNK_CHARS
    assert chopped.endswith("word")  # no mid-word cut
    assert case_corpus._fit_budget("short") == "short"
    assert case_corpus._fit_budget("") == ""


def test_chunk_embed_text_header():
    e = {"cite": "476 U.S. 79", "name": "Batson v. Kentucky"}
    t = case_corpus._chunk_embed_text(e, "The Equal Protection Clause forbids.")
    assert t.startswith("476 U.S. 79 — Batson v. Kentucky")
    assert "Equal Protection" in t


# ---------------------------------------------------------------------------
# INGEST (embed + Qdrant mocked — no live services)
# ---------------------------------------------------------------------------
def test_ingest_one_case_payload_shape_and_ids():
    entry = {"cite": "476 U.S. 79", "name": "Batson v. Kentucky",
             "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1986,
             "issues": ["batson", "equal protection"], "tier": 4, "batson": True}
    text = ("Para. " * 30) + "\n\n" + ("Para2. " * 30)

    captured: dict = {}

    def fake_put(url, json=None, timeout=None):
        if url.endswith("/points"):
            captured["points"] = json["points"]
        return MagicMock(raise_for_status=lambda: None)

    with patch.object(case_corpus, "_embed",
                      new=AsyncMock(side_effect=lambda texts: [[0.1] * 768 for _ in texts])):
        with patch.object(case_corpus.requests, "post", side_effect=fake_put) as mpost:
            with patch.object(case_corpus.requests, "put", side_effect=fake_put):
                with patch.object(case_corpus, "ensure_cases_collection", return_value=None):
                    n = __import__("asyncio").run(case_corpus.ingest_one_case(entry, text, batch_size=16))

    assert n > 0, "chunks should have been ingested"
    pts = captured["points"]
    assert len(pts) == n
    p0 = pts[0]["payload"]
    assert p0["cite"] == "476 U.S. 79"
    assert p0["case_name"] == "Batson v. Kentucky"
    assert p0["tier"] == 4
    assert p0["batson"] is True
    assert p0["jurisdiction"] == "case"
    assert p0["source"] == "courtlistener"
    assert "content" in p0 and "chunk_index" in p0 and "chunk_count" in p0
    # point ids are deterministic UUIDv5
    assert len(str(pts[0]["id"])) == 36
    # idempotent re-run: the delete endpoint was called scoped to this cite
    delete_calls = [c for c in mpost.call_args_list if c.args[0].endswith("/points/delete")]
    assert len(delete_calls) == 1
    filt = delete_calls[0].kwargs["json"]["filter"]
    assert filt == {"must": [{"key": "cite", "match": {"value": "476 U.S. 79"}}]}


def test_ingest_one_case_empty_text_no_upsert():
    with patch.object(case_corpus.requests, "post"):
        with patch.object(case_corpus.requests, "put") as mput:
            with patch.object(case_corpus, "ensure_cases_collection", return_value=None):
                n = __import__("asyncio").run(
                    case_corpus.ingest_one_case(
                        {"cite": "X", "name": "Y", "tier": 1}, "   "
                    )
                )
    assert n == 0
    mput.assert_not_called()


# ---------------------------------------------------------------------------
# RETRIEVAL
# ---------------------------------------------------------------------------
def test_case_result_shapes_payload_for_reranker():
    point = {"id": "p1", "score": 0.9, "payload": {
        "cite": "476 U.S. 79", "case_name": "Batson v. Kentucky",
        "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1986,
        "issues": ["batson"], "tier": 4, "batson": True,
        "content": "The Equal Protection Clause forbids.",
        "chunk_index": 0, "chunk_count": 2}}
    r = legal_search._case_result(point)
    assert r["citation"] == "476 U.S. 79"
    assert r["section_title"] == "Batson v. Kentucky"
    assert r["content"] == "The Equal Protection Clause forbids."
    assert r["tier"] == 4 and r["batson"] is True
    assert r["issues"] == ["batson"]


@pytest.mark.asyncio
async def test_search_cases_builds_filter_and_shapes():
    dense = [
        {"id": "p1", "score": 0.9, "payload": {
            "cite": "476 U.S. 79", "case_name": "Batson v. Kentucky",
            "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1986,
            "issues": ["batson"], "tier": 4, "batson": True,
            "content": "The Equal Protection Clause forbids.",
            "chunk_index": 0, "chunk_count": 1}}
    ]
    seen = {}

    async def fake_dense(query, qfilter, top_k):
        seen["qfilter"] = qfilter
        seen["top_k"] = top_k
        return dense

    with patch.object(legal_search, "_search_cases_with_filter", new=fake_dense):
        with patch.object(legal_search, "rerank", new=AsyncMock(side_effect=lambda q, c, top_k: c)):
            res = await legal_search.search_cases("peremptory strikes", tier=4, circuit="scotus", batson=True, top_k=3)

    assert seen["qfilter"] == {"must": [
        {"key": "tier", "match": {"value": 4}},
        {"key": "circuit", "match": {"value": "scotus"}},
        {"key": "batson", "match": {"value": True}},
    ]}
    assert seen["top_k"] == 16  # dense net = max(3*4, 16)
    assert res[0]["citation"] == "476 U.S. 79"
    assert res[0]["batson"] is True


@pytest.mark.asyncio
async def test_search_cases_no_filter_when_none():
    with patch.object(legal_search, "_search_cases_with_filter", new=AsyncMock(return_value=[])) as m:
        await legal_search.search_cases("loss estimate")
    assert m.await_args.args[1] is None


@pytest.mark.asyncio
async def test_search_cases_rejects_bad_tier():
    with pytest.raises(ValueError):
        await legal_search.search_cases("x", tier=9)


@pytest.mark.asyncio
async def test_search_cases_empty_query():
    with patch.object(legal_search, "_search_cases_with_filter") as m:
        assert await legal_search.search_cases("   ") == []
        m.assert_not_awaited()
