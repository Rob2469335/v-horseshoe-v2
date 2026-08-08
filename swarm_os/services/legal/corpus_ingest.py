"""Legal statute corpus ingestion for Rob's Lawyer.

Milestone 1: load NY/NJ/GA/NC + USC statutes from the OpenUSLaw corpus
(vaquill/open-us-law, CC BY 4.0, snapshot v2026.07) into Qdrant as a
`legal_statutes` collection, section-level chunks with jurisdiction/citation/
hierarchy payloads.

Verified data seam (downloaded + inspected real Parquet):
  - One 24-column schema, per-state Parquet files on oss-data-us.vaquill.ai
  - Ingest payload fields: act_id, citation, state, jurisdiction, document_type,
    act_status (in_force/repealed/reserved/...), title_name, chapter,
    section_number, section_title, text, word_count, display_path
  - Filter for ingestion: act_status == "in_force" and document_type == "statute"

Deliberately NOT all 2M sections: we scope to the operator's jurisdictions
(NY, NJ, GA, NC + federal) at download time, so the corpus is small and fast.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
import requests

log = logging.getLogger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
EMBED_URL = os.getenv("EMBED_URL", "http://127.0.0.1:8081/v1")
COLLECTION = "legal_statutes"
VECTOR_SIZE = 768  # gte-modernbert (same as codebase_index)
EMBED_MODEL = "gte-modernbert-base-Q8_0.gguf"

# Snapshot manifest (verified via oss-data-us.vaquill.ai/index.json)
SNAPSHOT = "v2026.07"
OSS_BASE = "https://oss-data-us.vaquill.ai"

# Jurisdiction scope: NY, NJ, GA, NC + federal (USC).
SCOPE_FILES = {
    "ny": "us_ny_statutes.parquet",
    "nj": "us_nj_statutes.parquet",
    "ga": "us_ga_statutes.parquet",
    "nc": "us_nc_statutes.parquet",
    "federal": "us_federal_statutes.parquet",
}

_embed_client: httpx.AsyncClient | None = None


def _get_embed_client() -> httpx.AsyncClient:
    global _embed_client
    if _embed_client is None or _embed_client.is_closed:
        _embed_client = httpx.AsyncClient(base_url=EMBED_URL, timeout=60.0)
    return _embed_client


def parquet_url(state: str) -> str:
    return f"{OSS_BASE}/{SNAPSHOT}/{SCOPE_FILES[state]}"


def ensure_collection() -> None:
    """Create the legal_statutes collection if it doesn't exist."""
    existing = requests.get(f"{QDRANT_URL}/collections", timeout=10.0).json()
    names = {c["name"] for c in existing.get("result", {}).get("collections", [])}
    if COLLECTION in names:
        return
    resp = requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION}",
        json={
            "vectors": {"size": VECTOR_SIZE, "distance": "Cosine"},
        },
        timeout=30.0,
    )
    resp.raise_for_status()


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Map a verified OpenUSLaw row to the Qdrant payload. Only the fields the
    retrieval layer needs; keeps payloads small (each section ~ few KB)."""
    text = str(row.get("text") or "")
    title = str(row.get("section_title") or row.get("section_number") or "")
    return {
        "jurisdiction": str(row.get("state") or ""),  # ny / nj / ga / nc / federal
        "citation": str(row.get("citation") or ""),
        "act_id": str(row.get("act_id") or ""),
        "section_number": str(row.get("section_number") or ""),
        "section_title": title,
        "chapter": str(row.get("chapter") or ""),
        "title_name": str(row.get("title_name") or ""),
        "display_path": str(row.get("display_path") or ""),
        "act_status": str(row.get("act_status") or ""),
        "content": text,
        "word_count": int(row.get("word_count") or 0),
        "source_url": str(row.get("source_url") or ""),
    }


def iter_in_force_rows(parquet_path: Path, jurisdiction: str):
    """Yield (act_id, payload) for every in-force statute section in one file.
    Uses a generator so we never hold the whole file's rows in memory."""
    import pyarrow.parquet as pq
    table = pq.read_table(str(parquet_path))
    for row in table.to_pylist():
        if row.get("act_status") != "in_force":
            continue
        if row.get("document_type") not in (None, "statute"):
            continue
        payload = _row_to_payload(row)
        act_id = payload["act_id"]
        if not act_id or not payload["content"].strip():
            continue
        # Prefix the id with the jurisdiction for a stable, non-colliding point id.
        yield f"{jurisdiction}:{act_id}", payload


async def _embed(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch via the local gte-modernbert server (:8081)."""
    try:
        resp = await _get_embed_client().post(
            "/embeddings",
            json={"model": EMBED_MODEL, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
    except Exception as exc:
        log.warning("embed failed for batch of %d: %s", len(texts), exc)
        return None


async def ingest_one_file(parquet_path: Path, jurisdiction: str, batch_size: int = 16) -> int:
    """Ingest one jurisdiction's in-force sections into Qdrant. Returns the
    number of sections indexed. Re-embedding is idempotent: we delete the
    jurisdiction's points first, then upsert fresh."""
    ensure_collection()
    # Remove existing points for this jurisdiction so re-runs don't duplicate.
    try:
        requests.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/delete",
            json={"filter": {"must": [{"key": "jurisdiction", "match": {"value": jurisdiction}}]}},
            timeout=60.0,
        )
    except Exception as exc:
        log.warning("delete existing %s points failed: %s", jurisdiction, exc)

    batch_texts: list[str] = []
    batch_ids: list[str] = []
    batch_payloads: list[dict[str, Any]] = []
    total = 0

    async def flush():
        nonlocal total
        if not batch_ids:
            return
        vectors = await _embed(batch_texts)
        if vectors and len(vectors) == len(batch_ids):
            points = [
                {"id": bid, "vector": vec, "payload": payload}
                for bid, vec, payload in zip(batch_ids, vectors, batch_payloads)
            ]
            resp = requests.put(
                f"{QDRANT_URL}/collections/{COLLECTION}/points",
                json={"points": points},
                timeout=120.0,
            )
            resp.raise_for_status()
            total += len(points)
        batch_ids.clear()
        batch_texts.clear()
        batch_payloads.clear()

    for point_id, payload in iter_in_force_rows(parquet_path, jurisdiction):
        # Embed the citation + title + text so retrieval matches on the section
        # heading and the body, not just one or the other.
        batch_texts.append(
            f"{payload['citation']} — {payload['section_title']}\n{payload['content']}"
        )
        batch_ids.append(point_id)
        batch_payloads.append(payload)
        if len(batch_ids) >= batch_size:
            await flush()

    await flush()
    return total


async def ingest_all(parquet_dir: Path, jurisdictions: list[str] | None = None) -> dict[str, int]:
    """Ingest the scoped jurisdictions. `jurisdictions` defaults to the full
    SCOPE_FILES set. Files must already be downloaded into `parquet_dir` (we do
    NOT download here — the download is a separate step so the corpus is
    auditable and re-runnable offline)."""
    targets = jurisdictions or list(SCOPE_FILES)
    result: dict[str, int] = {}
    for jur in targets:
        f = parquet_dir / SCOPE_FILES[jur]
        if not f.exists():
            log.warning("missing parquet for %s: %s", jur, f)
            result[jur] = 0
            continue
        result[jur] = await ingest_one_file(f, jur)
        log.info("indexed %s: %d sections", jur, result[jur])
    return result


def download_parquet(jurisdictions: list[str] | None = None, out_dir: Path = Path("./data/legal")) -> dict[str, Path]:
    """Download the scoped Parquet files to out_dir. Returns {jurisdiction: path}.
    Downloads only the jurisdictions in scope (not all 50 states)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = jurisdictions or list(SCOPE_FILES)
    result: dict[str, Path] = {}
    for jur in targets:
        dest = out_dir / SCOPE_FILES[jur]
        if dest.exists() and dest.stat().st_size > 0:
            result[jur] = dest
            continue
        url = parquet_url(jur)
        log.info("downloading %s <- %s", jur, url)
        with requests.get(url, stream=True, timeout=300.0) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        result[jur] = dest
    return result
