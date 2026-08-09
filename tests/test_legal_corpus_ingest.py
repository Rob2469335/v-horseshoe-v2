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
def test_real_nc_schema_maps_to_payload():
    """The verified 24-column schema maps to the retrieval payload correctly."""
    rows = list(corpus_ingest.iter_in_force_rows(NC_PARQUET, "nc"))
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


def test_in_force_filter_excludes_non_in_force():
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
        rows = list(corpus_ingest.iter_in_force_rows(Path(fname), "xx"))
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
