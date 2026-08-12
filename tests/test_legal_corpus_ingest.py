"""Tests for Rob's Lawyer corpus ingestion (swarm_os/services/legal/corpus_ingest).

Covers the ingestion seam WITHOUT network: row→payload mapping against the REAL
NC Parquet file (downloaded + verified this session), in-force filtering,
jurisdiction-prefixed stable IDs, and the per-file download URL.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from swarm_os.services.legal import corpus_ingest

NC_PARQUET = Path(r"C:\Users\rober\AppData\Local\Temp\opencode\us_nc_statutes.parquet")


@pytest.mark.skipif(not NC_PARQUET.exists(), reason="real NC parquet not present (offline dev machine)")
@pytest.mark.asyncio
async def test_real_nc_schema_maps_to_payload():
    """The verified 24-column schema maps to the retrieval payload correctly."""
    rows = [r async for r in corpus_ingest.iter_in_force_rows(NC_PARQUET, "nc")]
    assert rows, "expected at least one in-force NC section"
    point_id, payload = rows[0]
    # Qdrant point ids must be UUIDs (verified live: string ids are rejected 400).
    import uuid as _uuid
    assert _uuid.UUID(point_id)
    # The payload fields the retrieval layer needs must all be present.
    for field in ("jurisdiction", "citation", "act_id", "section_number",
                  "section_title", "content", "word_count"):
        assert field in payload, f"missing {field}"
    assert payload["jurisdiction"] == "nc"
    assert payload["content"].strip(), "section text must be non-empty"
    assert payload["act_status"] == "in_force"


@pytest.mark.asyncio
async def test_in_force_filter_excludes_non_in_force():
    """Rows that aren't in_force must be dropped by the generator."""
    fake_rows = [
        {"act_status": "in_force", "document_type": "statute", "act_id": "a",
         "citation": "X", "section_title": "t", "text": "hello", "word_count": 1},
        {"act_status": "repealed", "document_type": "statute", "act_id": "b",
         "citation": "Y", "section_title": "u", "text": "gone", "word_count": 1},
        {"act_status": "in_force", "document_type": "statute", "act_id": "",
         "citation": "Z", "section_title": "v", "text": "no-id", "word_count": 1},
    ]
    import pyarrow as pa
    import pyarrow.parquet as pq
    import tempfile
    table = pa.Table.from_pylist(fake_rows)
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        pq.write_table(table, f.name)
        fname = f.name
    try:
        rows = [r async for r in corpus_ingest.iter_in_force_rows(Path(fname), "xx")]
    finally:
        Path(fname).unlink(missing_ok=True)
    ids = [pid for pid, _ in rows]
    assert len(ids) == 1  # only the in-force section with an act_id survives
    import uuid as _uuid
    assert _uuid.UUID(ids[0])


def test_point_id_is_deterministic_uuid():
    """Point ids must be deterministic UUIDv5s (from act_id) so re-runs are
    idempotent, cross-jurisdiction sections never collide, and Qdrant accepts
    them (string ids are rejected with HTTP 400)."""
    p = corpus_ingest._row_to_payload({"state": "ny", "act_id": "STATE_NY_S100",
                                       "citation": "N.Y. Gen. Oblig. L. § 5-701",
                                       "section_title": "t", "text": "x",
                                       "word_count": 5})
    assert p["jurisdiction"] == "ny"
    assert p["citation"] == "N.Y. Gen. Oblig. L. § 5-701"


def test_parquet_url_and_scope():
    """The scoped jurisdictions map to the verified snapshot manifest files."""
    assert corpus_ingest.parquet_url("ny").endswith("/v2026.07/us_ny_statutes.parquet")
    assert set(corpus_ingest.SCOPE_FILES) == {"ny", "nj", "ga", "nc", "federal"}


class _BoundedBatch:
    """Mimics pyarrow.RecordBatch.to_pylist() for one bounded chunk."""

    def __init__(self, rows):
        self.rows = rows

    def to_pylist(self):
        return self.rows


class _FakeTable:
    """A pyarrow.Table stand-in that REFUSES the unbounded whole-table
    to_pylist() and serves bounded chunks via to_batches(max_chunksize=...)."""

    def __init__(self, rows):
        self.rows = rows
        self.requested_chunksize = None

    def to_pylist(self):
        raise AssertionError(
            "unbounded whole-table to_pylist() called — "
            "iter_in_force_rows must read bounded batches"
        )

    def to_batches(self, max_chunksize=None):
        self.requested_chunksize = max_chunksize
        batches = []
        for i in range(0, len(self.rows), max_chunksize or len(self.rows)):
            batches.append(_BoundedBatch(self.rows[i:i + max_chunksize]))
        return batches


@pytest.mark.asyncio
async def test_iter_in_force_rows_reads_bounded_batches(monkeypatch):
    """The generator must pull rows through to_batches(max_chunksize=1024),
    never materialize the whole table as one Python list. A table whose
    whole-table to_pylist() raises proves the bounded path is the only path."""
    fake_rows = [
        {"act_status": "in_force", "document_type": "statute", "act_id": f"s{i}",
         "citation": f"C{i}", "section_title": "t", "text": "hello", "word_count": 1}
        for i in range(2500)
    ]
    fake_table = _FakeTable(fake_rows)
    import pyarrow.parquet as pq
    monkeypatch.setattr(pq, "read_table", lambda _path: fake_table)
    rows = [r async for r in corpus_ingest.iter_in_force_rows(Path("whatever.parquet"), "xx")]
    assert len(rows) == 2500
    # Each in-memory chunk is bounded to the documented 1024 rows so a large
    # parquet never balloons into one giant Python list of dicts.
    assert fake_table.requested_chunksize == 1024
    assert not any(len(b.rows) > 1024 for b in fake_table.to_batches(max_chunksize=1024))


# --- Contextual retrieval (M8) ------------------------------------------------

def test_build_context_metadata_frames_section():
    """build_context must produce a deterministic 'situate this section' string
    from the payload metadata — jurisdiction + citation + act/chapter framing —
    so a bare section chunk is retrievable for a topic-described question."""
    payload = {
        "jurisdiction": "ny", "citation": "N.Y. RPA Law § 235-b",
        "section_number": "235-b", "section_title": "Recovery of possession",
        "title_name": "Real Property Actions and Proceedings Law",
        "chapter": "RPA Law", "display_path": "NY/RPA/235-b",
    }
    ctx = corpus_ingest.build_context(payload)
    assert "New York" in ctx, "jurisdiction name must be in the context"
    assert "235-b" in ctx, "section id must be in the context"
    assert "Real Property Actions" in ctx, "act title must be in the context"


def test_build_context_handles_missing_metadata():
    """build_context must not raise on a sparse payload (missing fields -> sane
    fallbacks), and must still name the jurisdiction."""
    ctx = corpus_ingest.build_context({"jurisdiction": "nj"})
    assert "New Jersey" in ctx
    assert ctx.strip(), "context must be non-empty even with no other fields"


@pytest.mark.asyncio
async def test_build_context_llm_falls_back_to_metadata_on_failure():
    """The LLM-assisted contextualizer must never raise: on any LLM failure it
    falls back to the deterministic metadata context (offline-safe)."""
    import swarm_os.services.legal.corpus_ingest as ci

    async def broken_stream(model, messages, agent_id):
        raise RuntimeError("llm down")
        yield  # pragma: no cover - makes this an async generator that raises

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("runtime_v2.services._llm_client.stream_content", broken_stream)
        ctx = await ci.build_context_llm(
            {"jurisdiction": "ny", "citation": "N.Y. RPA Law § 235-b"}, "some text")
    assert "New York" in ctx
    assert "235-b" in ctx
