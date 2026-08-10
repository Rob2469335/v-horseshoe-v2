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
import uuid
from pathlib import Path
from typing import Any

import asyncio
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

# The embed server (llama.cpp `--embedding`) rejects ANY request whose total
# input exceeds its PHYSICAL batch size (verified live: "input (58418 tokens) is
# too large to process, increase the physical batch size (current: 8192)").
# The repo's codebase indexer solved the same class of bug with word-chopping
# per text. For statutes (full sections can be thousands of tokens) we must
# BOTH chop each text to a per-text budget AND keep the batch small enough that
# batch × budget stays under 8192 tokens.
_EMBED_BATCH_BUDGET_CHARS = 4000   # ~1k tokens per text at ~4 chars/token
_MAX_BATCH_TOKENS = 8192           # llama.cpp physical batch size (verified)

# Jurisdiction scope: NY, NJ, GA, NC + federal (USC).
SCOPE_FILES = {
    "ny": "us_ny_statutes.parquet",
    "nj": "us_nj_statutes.parquet",
    "ga": "us_ga_statutes.parquet",
    "nc": "us_nc_statutes.parquet",
    "federal": "us_federal_statutes.parquet",
}


def _fit_budget(text: str) -> str:
    """Word-chopping budget (mirrors the codebase indexer's _fit_token_budget):
    never send the embed model a single text that could overflow its context.
    Preserves whole words up to the budget."""
    if not text or len(text) <= _EMBED_BATCH_BUDGET_CHARS:
        return text
    out: list[str] = []
    n = 0
    for w in text.split():
        if n + len(w) + 1 > _EMBED_BATCH_BUDGET_CHARS:
            break
        out.append(w)
        n += len(w) + 1
    return " ".join(out)

_embed_client: httpx.AsyncClient | None = None
_qdrant_client: httpx.AsyncClient | None = None


def _get_qdrant_client() -> httpx.AsyncClient:
    global _qdrant_client
    if _qdrant_client is None or _qdrant_client.is_closed:
        _qdrant_client = httpx.AsyncClient(base_url=QDRANT_URL, timeout=120.0)
    return _qdrant_client


def _get_embed_client() -> httpx.AsyncClient:
    global _embed_client
    if _embed_client is None or _embed_client.is_closed:
        _embed_client = httpx.AsyncClient(base_url=EMBED_URL, timeout=60.0)
    return _embed_client


def parquet_url(state: str) -> str:
    return f"{OSS_BASE}/{SNAPSHOT}/{SCOPE_FILES[state]}"


async def ensure_collection() -> None:
    """Create the legal_statutes collection if it doesn't exist."""
    client = _get_qdrant_client()
    existing = (await client.get("/collections")).json()
    names = {c["name"] for c in existing.get("result", {}).get("collections", [])}
    if COLLECTION in names:
        return
    resp = await client.put(
        f"/collections/{COLLECTION}",
        json={
            "vectors": {"size": VECTOR_SIZE, "distance": "Cosine"},
        },
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
        "title_number": str(row.get("title_number") or ""),
        "display_path": str(row.get("display_path") or ""),
        "act_status": str(row.get("act_status") or ""),
        "content": text,
        "word_count": int(row.get("word_count") or 0),
        "source_url": str(row.get("source_url") or ""),
    }


async def iter_in_force_rows(parquet_path: Path, jurisdiction: str, title_filter: str | None = None):
    """Async generator of (act_id, payload) for every in-force statute section in
    one file. Never holds the whole file's rows in memory (bounded batches) and
    never blocks the event loop: pq.read_table (a full disk read of the parquet)
    runs in a thread.
    `title_filter` (e.g. "18") restricts to one USC title — used for a scoped
    high-value ingest (Title 18 criminal code) without embedding all ~46K
    federal sections."""
    import pyarrow.parquet as pq
    table = await asyncio.to_thread(pq.read_table, str(parquet_path))
    for batch in table.to_batches(max_chunksize=1024):
        for row in batch.to_pylist():
            if row.get("act_status") != "in_force":
                continue
            if row.get("document_type") not in (None, "statute"):
                continue
            if title_filter and str(row.get("title_number") or "") != title_filter:
                continue
            payload = _row_to_payload(row)
            act_id = payload["act_id"]
            if not act_id or not payload["content"].strip():
                continue
            # Qdrant point IDs must be an unsigned integer or UUID (verified live:
            # string ids like "nc:STATE_NC_..." are rejected with HTTP 400). Use a
            # deterministic UUIDv5 from the act_id so re-runs are idempotent and
            # cross-jurisdiction sections never collide.
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"openuslaw:{jurisdiction}:{act_id}"))
            yield point_id, payload


async def _embed(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch via the local gte-modernbert server (:8081).

    Retries once on a transient 5xx — the embed server returns 500 while it is
    warming up / busy right after Qdrant or llama.cpp comes back, and without a
    retry the whole batch is silently dropped (the indexer's 'silent-drop' bug
    class). A 4xx is not retried (it's a real request error).
    """
    last_exc: Exception | None = None
    for attempt in (0, 1):
        try:
            # Word-choop every text so an oversized section can't reject the
            # whole batch (the codebase indexer's proven fix for the same
            # "input too large" 500).
            fit_texts = [_fit_budget(t) for t in texts]
            resp = await _get_embed_client().post(
                "/embeddings",
                json={"model": EMBED_MODEL, "input": fit_texts},
                headers={"Authorization": "Bearer llama"},  # llama.cpp serve --api-key llama
            )
            if resp.status_code >= 500:
                last_exc = RuntimeError(f"embed server {resp.status_code}: {resp.text[:200]}")
                await asyncio.sleep(1.0 + attempt)
                continue
            resp.raise_for_status()
            data = resp.json()["data"]
            return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
        except Exception as exc:
            last_exc = exc
            if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None \
                    and exc.response.status_code < 500:
                break  # 4xx: real error, don't retry
            await asyncio.sleep(1.0 + attempt)
    log.warning("embed failed for batch of %d after retries: %s", len(texts), last_exc)
    return None


async def ingest_one_file(parquet_path: Path, jurisdiction: str, batch_size: int = 16,
                          title_filter: str | None = None) -> int:
    """Ingest one jurisdiction's in-force sections into Qdrant. Returns the
    number of sections indexed. Re-embedding is idempotent: we delete the
    jurisdiction's (or title-scoped) points first, then upsert fresh.

    `title_filter` (e.g. "18") restricts both the delete and the ingest to one
    USC title, so a scoped Title-18 run never wipes the already-ingested
    federal sections outside that title.

    Batches are flushed by TOKEN BUDGET (not fixed count): the embed server's
    physical batch size is 8192 tokens (verified live), so a fixed-count batch
    of long statute sections blows past it and 500s the whole batch. We estimate
    each batch's tokens (~4 chars/token) and flush before it approaches 8192.
    """
    await ensure_collection()
    # Remove existing points for this jurisdiction (or title) so re-runs don't
    # duplicate — scoped to the filter so a title-scoped run is additive.
    delete_filter: dict = {"must": [{"key": "jurisdiction", "match": {"value": jurisdiction}}]}
    if title_filter:
        delete_filter["must"].append({"key": "title_number", "match": {"value": title_filter}})
    try:
        await _get_qdrant_client().post(
            f"/collections/{COLLECTION}/points/delete",
            json={"filter": delete_filter},
        )
    except Exception as exc:
        log.warning("delete existing %s points failed: %s", jurisdiction, exc)

    batch_texts: list[str] = []
    batch_ids: list[str] = []
    batch_payloads: list[dict[str, Any]] = []
    batch_chars = 0
    total = 0

    async def flush():
        nonlocal total, batch_chars
        if not batch_ids:
            return
        vectors = await _embed(batch_texts)
        if vectors and len(vectors) == len(batch_ids):
            points = [
                {"id": bid, "vector": vec, "payload": payload}
                for bid, vec, payload in zip(batch_ids, vectors, batch_payloads)
            ]
            resp = await _get_qdrant_client().put(
                f"/collections/{COLLECTION}/points",
                json={"points": points},
            )
            resp.raise_for_status()
            total += len(points)
        batch_ids.clear()
        batch_texts.clear()
        batch_payloads.clear()
        batch_chars = 0

    async for point_id, payload in iter_in_force_rows(parquet_path, jurisdiction, title_filter=title_filter):
        # Embed the citation + title + text so retrieval matches on the section
        # heading and the body, not just one or the other.
        text = f"{payload['citation']} — {payload['section_title']}\n{payload['content']}"
        # ~4 chars/token; keep a 15% headroom under the 8192 physical batch size.
        approx_tokens = max(1, len(text) // 4)
        if batch_ids and batch_chars // 4 + approx_tokens >= int(_MAX_BATCH_TOKENS * 0.85):
            await flush()
        batch_texts.append(text)
        batch_ids.append(point_id)
        batch_payloads.append(payload)
        batch_chars += len(text)
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


def run_ingest_cli() -> None:
    """CLI entrypoint for the DETACHED background ingestion process.

    Logs every step to data/legal/ingest.log with flush=True so a parent shell
    teardown never loses progress, and so an operator can tail the file to watch
    a long (multi-jurisdiction) ingest finish outside any single command window.
    Writes a completion marker file on success.
    """
    import asyncio
    import logging

    log_dir = Path("./data/legal")
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_dir / "ingest.log", encoding="utf-8")],
    )
    # Keep httpx transport logs quiet in the file; we log our own progress.
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    asyncio.run(_ingest_all_cli(log_dir))


async def _ingest_all_cli(log_dir: Path) -> None:
    counts = await ingest_all(log_dir)
    line = f"INGEST COMPLETE: {counts} total={sum(counts.values())}"
    log.info(line)
    (log_dir / "ingest.done").write_text(line + "\n", encoding="utf-8")


if __name__ == "__main__":
    # Module entrypoint for the DETACHED background ingestion process. Launch as
    # `python -m swarm_os.services.legal.corpus_ingest` — never via `-c`, which
    # PowerShell's Start-Process -ArgumentList mangles (the silent-death bug).
    run_ingest_cli()
