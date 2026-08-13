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
        # STATUTE CURRENCY (rec 10): every point carries which OpenUSLaw snapshot
        # it came from + the section's status in that snapshot. OpenUSLaw is "a
        # dated snapshot, not a live feed" (quarterly cadence) — so the advisor
        # can answer "as of WHAT law?" and a later snapshot diff can flag a
        # section that flips in_force -> repealed. act_status also gates ingest
        # (only in_force sections enter), but the stored value keeps the truth.
        "snapshot": SNAPSHOT,
        "content": text,
        "word_count": int(row.get("word_count") or 0),
        "source_url": str(row.get("source_url") or ""),
        "context": "",
    }


# Contextual retrieval (Anthropic 2024, "Introducing Contextual Retrieval"):
# prepend a short "situate this chunk in its document" string to the EMBED TEXT
# so a bare section ("the tenant shall be entitled...") is retrievable for a
# question that never names the section ("what notice must a landlord give?").
# Two modes:
#   - METADATA (default): deterministic context built from the payload's own
#     citation/hierarchy fields — free, offline, zero LLM calls. The embed
#     server already receives "citation — title", this adds the jurisdiction +
#     act/chapter framing so the embedding knows WHO the speaker is.
#   - LLM-ASSISTED: `SWARM_LEGAL_CONTEXT_LLM=1` — one short stream_content call
#     per section through the local 0.8B summarizer (:8084) or DeepSeek, the
#     Anthropic template ("This is a statute section from..."). Expensive for a
#     full corpus (~115K sections) — the metadata mode is the default.
_CONTEXT_TEMPLATE = (
    "Statute section from {jurisdiction_upper} law. "
    "Citation: {citation}. Section {section_number} ({section_title}) of {act_title}. "
    "Part of {chapter_path}. This section defines rules for {section_topic}."
)


def build_context(payload: dict[str, Any]) -> str:
    """Deterministic metadata-based context for a statute section — the offline
    'contextual retrieval' upgrade. Threads the jurisdiction/act/chapter framing
    that a bare section chunk lacks, so dense retrieval can match a question
    that describes the topic without naming the section."""
    jur = payload.get("jurisdiction", "")
    jur_upper = {"ny": "New York", "nj": "New Jersey", "ga": "Georgia",
                 "nc": "North Carolina", "federal": "Federal (U.S. Code)"}.get(jur, jur.upper())
    act_title = payload.get("title_name") or payload.get("title_number") or "law"
    chapter_path = payload.get("display_path") or payload.get("chapter") or ""
    topic = (payload.get("section_title") or payload.get("section_number") or "this area").strip()
    return _CONTEXT_TEMPLATE.format(
        jurisdiction_upper=jur_upper,
        citation=payload.get("citation", ""),
        section_number=payload.get("section_number", ""),
        section_title=payload.get("section_title", ""),
        act_title=act_title,
        chapter_path=chapter_path,
        section_topic=topic,
    )


async def build_context_llm(payload: dict[str, Any], section_text: str) -> str:
    """LLM-assisted contextual context (SWARM_LEGAL_CONTEXT_LLM=1): a short
    'situate this section' string via the local 0.8B summarizer. Falls back to
    the deterministic build_context on any failure (never raises)."""
    try:
        from runtime_v2.services import _llm_client as llm
        system = (
            "You situate a statute section within its governing law. In <context> "
            "write 2-3 sentences: what jurisdiction, what act/chapter, and what "
            "subject the section governs — WITHOUT quoting the section text."
        )
        snippet = (section_text or "")[:600]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                f"Jurisdiction: {payload.get('jurisdiction', '')}\n"
                f"Citation: {payload.get('citation', '')}\n"
                f"Title/act: {payload.get('title_name', '')} {payload.get('title_number', '')}\n"
                f"Chapter: {payload.get('chapter', '')}\n"
                f"Section text:\n<chunk>\n{snippet}\n</chunk>"
            )},
        ]
        model = llm._analysis_cloud_model() if llm._analysis_cloud_enabled() else "qwen3.5-4b"
        parts: list[str] = []
        async for chunk, kind in llm.stream_content(model, messages, agent_id="legal_context"):
            if kind == "content":
                parts.append(chunk or "")
        ctx = "".join(parts).strip()
        if ctx:
            return f"This is a statute section from {payload.get('jurisdiction', '').upper()} law. {ctx}"
    except Exception as exc:
        log.debug("LLM context failed for %s, using metadata: %s",
                  payload.get("citation", ""), exc)
    return build_context(payload)


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
                          title_filter: str | None = None, contextualize: bool = True) -> int:
    """Ingest one jurisdiction's in-force sections into Qdrant. Returns the
    number of sections indexed. Re-embedding is idempotent: we delete the
    jurisdiction's (or title-scoped) points first, then upsert fresh.

    `title_filter` (e.g. "18") restricts both the delete and the ingest to one
    USC title, so a scoped Title-18 run never wipes the already-ingested
    federal sections outside that title.

    `contextualize` (default True) prepends the metadata-based context string to
    the embed text AND stores it in the payload — the offline contextual-
    retrieval upgrade. Set SWARM_LEGAL_CONTEXT_LLM=1 for LLM-assisted context.

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
            # Qdrant can drop the connection mid-PUT under load/optimization
            # (RemoteProtocolError: "Server disconnected without sending a
            # response" — crashed the NY re-ingest TWICE at this exact line).
            # The upsert is idempotent (deterministic UUIDv5 point ids), so a
            # failed PUT can be safely re-sent. Bounded retry on transient
            # transport errors + 5xx; a 4xx (real request error) propagates
            # immediately, and a still-failing batch raises (fail-closed).
            last_exc: Exception | None = None
            for attempt in (0, 1, 2):
                try:
                    resp = await _get_qdrant_client().put(
                        f"/collections/{COLLECTION}/points",
                        json={"points": points},
                    )
                    if resp.status_code >= 500:
                        last_exc = RuntimeError(
                            f"qdrant upsert {resp.status_code}: {resp.text[:200]}")
                        await asyncio.sleep(1.0 + attempt)
                        continue
                    resp.raise_for_status()
                    total += len(points)
                    break
                except httpx.TransportError as exc:
                    last_exc = exc
                    await asyncio.sleep(1.0 + attempt)
            else:
                log.warning("qdrant upsert failed for batch of %d after retries: %s",
                            len(points), last_exc)
                raise RuntimeError(
                    f"qdrant upsert failed after retries: {last_exc}") from last_exc
        batch_ids.clear()
        batch_texts.clear()
        batch_payloads.clear()
        batch_chars = 0

    async for point_id, payload in iter_in_force_rows(parquet_path, jurisdiction, title_filter=title_filter):
        # CONTEXTUAL RETRIEVAL: situate the section before embedding so a bare
        # chunk is retrievable for a topic-described question. The context is
        # ALSO stored in the payload (retrieval can surface it) and prepended
        # to the embed text (so the embedding carries it).
        if contextualize:
            if os.getenv("SWARM_LEGAL_CONTEXT_LLM") == "1":
                context = await build_context_llm(payload, payload["content"])
            else:
                context = build_context(payload)
            payload["context"] = context
        else:
            payload["context"] = ""
        # Embed the context + citation + title + text so retrieval matches on the
        # section heading and the body, not just one or the other.
        text = f"{payload['context']}\n{payload['citation']} — {payload['section_title']}\n{payload['content']}"
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


async def ingest_all(parquet_dir: Path, jurisdictions: list[str] | None = None,
                     contextualize: bool = True) -> dict[str, int]:
    """Ingest the scoped jurisdictions. `jurisdictions` defaults to the full
    SCOPE_FILES set. Files must already be downloaded into `parquet_dir` (we do
    NOT download here — the download is a separate step so the corpus is
    auditable and re-runnable offline). `contextualize` (default True) enables
    the contextual-retrieval embed (context prepended to the embed text)."""
    targets = jurisdictions or list(SCOPE_FILES)
    result: dict[str, int] = {}
    for jur in targets:
        f = parquet_dir / SCOPE_FILES[jur]
        if not f.exists():
            log.warning("missing parquet for %s: %s", jur, f)
            result[jur] = 0
            continue
        result[jur] = await ingest_one_file(f, jur, contextualize=contextualize)
        log.info("indexed %s: %d sections", jur, result[jur])
    return result


async def backfill_payloads(batch_size: int = 2000) -> dict[str, Any]:
    """Backfill the `snapshot` payload field onto EXISTING statute points WITHOUT
    re-embedding (rec 10 statute-currency fix).

    The corpus was ingested before the snapshot-stamping upgrade, so every
    point lacks `snapshot` — making `corpus_scope` report an empty snapshot and
    `law_as_of` fall to "unknown". `set_payload` stamps the shared SNAPSHOT on a
    batch of point ids WITHOUT touching vectors — O(n) payload updates, fast for
    ~115K points, idempotent.

    NOTE: this does NOT backfill the `context` field used for contextual-
    retrieval embeddings. Contextual retrieval only matters at EMBED time — a
    payload-only `context` backfill would not change retrieval (the vectors were
    computed without it). That upgrade requires a full re-ingest
    (`ingest_all`, hours) and is intentionally NOT part of this fix.

    Returns {"updated": n, "jurisdictions": {jur: count}}.
    """
    from swarm_os.lib.vector.qdrant_store import QDRANT_URL
    from qdrant_client import AsyncQdrantClient
    client = AsyncQdrantClient(url=QDRANT_URL)
    updated = 0
    by_jur: dict[str, int] = {}
    offset: Any = None
    try:
        while True:
            resp = await client.scroll(COLLECTION, limit=batch_size, with_payload=True,
                                       offset=offset)
            points = resp[0]
            ids: list[Any] = []
            for point in points:
                payload = dict(point.payload or {})
                if payload.get("snapshot"):
                    continue  # already currency-stamped
                ids.append(point.id)
                updated += 1
                by_jur[payload.get("jurisdiction", "?")] = by_jur.get(payload.get("jurisdiction", "?"), 0) + 1
            if ids:
                await client.set_payload(
                    collection_name=COLLECTION,
                    payload={"snapshot": SNAPSHOT},
                    points=ids,
                )
            if resp[1] is None:
                break
            offset = resp[1]
    finally:
        await client.close()
    return {"updated": updated, "jurisdictions": by_jur}


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
    # Optional jurisdiction scope via INGEST_JURISDICTIONS (comma-separated) so
    # a re-run can target one jurisdiction (e.g. "ny") without re-doing the
    # already-completed ones. Unset = full scope.
    jurisdictions: list[str] | None = None
    env_scope = os.getenv("INGEST_JURISDICTIONS", "")
    if env_scope:
        jurisdictions = [j.strip() for j in env_scope.split(",") if j.strip()]
    counts = await ingest_all(log_dir, jurisdictions=jurisdictions)
    line = f"INGEST COMPLETE: {counts} total={sum(counts.values())}"
    log.info(line)
    (log_dir / "ingest.done").write_text(line + "\n", encoding="utf-8")


if __name__ == "__main__":
    # Module entrypoint for the DETACHED background ingestion process. Launch as
    # `python -m swarm_os.services.legal.corpus_ingest` — never via `-c`, which
    # PowerShell's Start-Process -ArgumentList mangles (the silent-death bug).
    run_ingest_cli()
