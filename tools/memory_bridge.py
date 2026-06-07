"""
memory_bridge.py
Semantic Memory Bridge — ties tools/event_log.py into VectorStore.

Role in the stack
-----------------
  EventLog (WAL) ──► MemoryBridge ──► VectorStore (Qdrant)
                           │
                           └──► Router.event_hint()   (proactive routing)

The bridge:
  1. Ingests raw LogEntry dicts from the EventLog JSONL file.
  2. Accumulates events into "chunks" (default: 8 events or a
     TASK_COMPLETE / record_failure boundary).
  3. Sends each chunk to a fast local model (mistral-nemo via Ollama)
     for a one-sentence semantic summary.
  4. Embeds the summary with EmbeddingService (nomic-embed-text).
  5. Upserts the vector into the `horseshoe_swarm_memory` Qdrant
     collection with rich metadata so the Router can query it.

Usage
-----
  bridge = MemoryBridge()

  # On-demand: drain everything since last watermark
  await bridge.ingest_new_events()

  # Continuous: run as a background task
  asyncio.create_task(bridge.watch_loop(interval_seconds=5))

  # Router integration: call before route_model()
  hint = await bridge.query_event_hint(event_type="WRITE", model="qwen2.5-coder:14b")
  # hint.suggested_avoid == True  →  route to 32b instead
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from swarm_os.services.embedding_service import EmbeddingService
from swarm_os.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
COLLECTION_NAME = "horseshoe_swarm_memory"
CHUNK_SIZE       = 8          # events per semantic summary
WATERMARK_FILE   = Path("logs/.memory_bridge_watermark")
OLLAMA_BASE_URL  = "http://localhost:11434"
SUMMARISER_MODEL = "mistral-nemo"   # fast 12-B; swap to any Ollama model
VECTOR_SIZE      = 768              # nomic-embed-text output dim

# Event types that always flush the current chunk immediately
FLUSH_TRIGGERS = {"TASK_COMPLETE", "record_failure", "AGENT_ERROR"}

# Payload fields that determine the "event type" key stored on the memory point
EVENT_TYPE_KEYS  = ("event_type", "type", "action", "kind")


# ── Data models ────────────────────────────────────────────────────────────────
@dataclass
class EventHint:
    """Returned by query_event_hint() to the Router."""
    suggested_avoid:  bool     = False
    suggested_prefer: str      = ""      # model name if one worked well
    failure_rate:     float    = 0.0
    evidence_count:   int      = 0
    top_summary:      str      = ""


@dataclass
class _Chunk:
    events:     List[Dict[str, Any]] = field(default_factory=list)
    model_tags:  List[str]           = field(default_factory=list)
    outcomes:    List[str]           = field(default_factory=list)   # "success"|"failure"
    event_types: List[str]           = field(default_factory=list)


# ── MemoryBridge ──────────────────────────────────────────────────────────────
class MemoryBridge:
    """
    Bridges the EventLog WAL into the VectorStore as Semantic Memories.

    Parameters
    ----------
    event_log_path : Path
        Path to the JSONL file written by tools/event_log.py.
        Defaults to  logs/event_log.jsonl  (relative to cwd).
    vector_store : VectorStore | None
        Pass an existing VectorStore, or None to create a fresh one
        targeting the `horseshoe_swarm_memory` collection.
    embedding_svc : EmbeddingService | None
        Pass an existing service, or None to create one.
    chunk_size : int
        Number of raw events to accumulate before summarising.
    """

    def __init__(
        self,
        event_log_path: Path | str = Path("logs/event_log.jsonl"),
        *,
        vector_store:   Optional[VectorStore]    = None,
        embedding_svc:  Optional[EmbeddingService] = None,
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        self.event_log_path = Path(event_log_path)
        self.chunk_size     = chunk_size

        self.vs  = vector_store  or VectorStore(
            collection_name=COLLECTION_NAME,
            vector_size=VECTOR_SIZE,
            use_memory=False,       # production: real Qdrant on localhost:6333
        )
        self.emb = embedding_svc or EmbeddingService(
            base_url=OLLAMA_BASE_URL,
            model="nomic-embed-text",
        )
        self._http = httpx.AsyncClient(timeout=60.0)
        self._watermark: int = self._load_watermark()
        self._active_chunk = _Chunk()

        logger.info(
            "MemoryBridge ready | log=%s | watermark=%d | collection=%s",
            self.event_log_path, self._watermark, COLLECTION_NAME,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    async def ingest_new_events(self) -> int:
        """
        Read events added to the log since the last watermark,
        chunk → summarise → embed → upsert.

        Returns the number of new memory points written.
        """
        raw = self._read_new_events()
        if not raw:
            return 0

        points_written = 0
        for entry in raw:
            self._active_chunk.events.append(entry)
            self._active_chunk.model_tags.append(
                entry.get("model") or entry.get("assigned_to") or ""
            )
            outcome = entry.get("outcome") or entry.get("status") or ""
            self._active_chunk.outcomes.append(str(outcome))
            etype = self._extract_event_type(entry)
            self._active_chunk.event_types.append(etype)

            should_flush = (
                len(self._active_chunk.events) >= self.chunk_size
                or etype in FLUSH_TRIGGERS
            )
            if should_flush:
                ok = await self._flush_chunk()
                if ok:
                    points_written += 1
                self._active_chunk = _Chunk()

        self._save_watermark()
        return points_written

    async def watch_loop(self, interval_seconds: float = 5.0) -> None:
        """Continuously poll the EventLog and ingest new events."""
        logger.info("MemoryBridge watch_loop started (interval=%.1fs)", interval_seconds)
        while True:
            try:
                n = await self.ingest_new_events()
                if n:
                    logger.debug("MemoryBridge: wrote %d memory points", n)
            except Exception as exc:
                logger.warning("MemoryBridge ingest error: %s", exc)
            await asyncio.sleep(interval_seconds)

    async def query_event_hint(
        self,
        event_type: str = "",
        model:      str = "",
        top_k:      int = 5,
    ) -> EventHint:
        """
        Query semantic memories to advise the Router before it picks a model.

        The query vector is built from a natural-language description of the
        current routing context so that similar past situations surface.
        """
        query_text = f"event_type:{event_type} model:{model} routing decision"
        try:
            q_vec = self.emb.embed(query_text)
        except Exception:
            return EventHint()

        results = self.vs.search(query_vector=q_vec, limit=top_k)
        if not results:
            return EventHint()

        failure_count   = 0
        success_count   = 0
        model_successes: Dict[str, int] = {}
        model_failures:  Dict[str, int] = {}

        for r in results:
            p = r.get("payload", {})
            if not p:
                continue
            dominant_outcome = p.get("dominant_outcome", "")
            models_in_chunk  = p.get("models", [])

            if dominant_outcome == "failure":
                failure_count += 1
                for m in models_in_chunk:
                    model_failures[m] = model_failures.get(m, 0) + 1
            else:
                success_count += 1
                for m in models_in_chunk:
                    model_successes[m] = model_successes.get(m, 0) + 1

        total = failure_count + success_count
        failure_rate = failure_count / total if total else 0.0

        # Suggest avoiding model if it appears only in failure memories
        suggest_avoid = (
            failure_rate >= 0.6
            and model
            and model_failures.get(model, 0) > model_successes.get(model, 0)
        )

        # Best alternative: most-successful model that isn't the candidate
        best_alt = max(
            (m for m in model_successes if m != model),
            key=lambda m: model_successes[m],
            default="",
        )

        top_summary = results[0].get("payload", {}).get("summary", "") if results else ""

        return EventHint(
            suggested_avoid  = suggest_avoid,
            suggested_prefer = best_alt,
            failure_rate     = round(failure_rate, 3),
            evidence_count   = len(results),
            top_summary      = top_summary,
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _read_new_events(self) -> List[Dict[str, Any]]:
        """Read JSONL lines beyond the current watermark."""
        if not self.event_log_path.exists():
            return []
        events: List[Dict[str, Any]] = []
        try:
            with self.event_log_path.open("r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh):
                    if lineno < self._watermark:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.debug("Skipping malformed log line %d", lineno)
            self._watermark += len(events)
        except OSError as exc:
            logger.warning("Could not read event log: %s", exc)
        return events

    def _load_watermark(self) -> int:
        try:
            return int(WATERMARK_FILE.read_text().strip())
        except (OSError, ValueError):
            return 0

    def _save_watermark(self) -> None:
        WATERMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATERMARK_FILE.write_text(str(self._watermark))

    @staticmethod
    def _extract_event_type(entry: Dict[str, Any]) -> str:
        for key in EVENT_TYPE_KEYS:
            if val := entry.get(key):
                return str(val)
        return "UNKNOWN"

    async def _summarise(self, chunk: _Chunk) -> str:
        """
        Send a compact event chunk to a fast Ollama model for a one-sentence
        semantic summary.  Falls back to a rule-based summary on any error.
        """
        snippet = json.dumps(
            [
                {
                    k: v for k, v in e.items()
                    if k in ("event_type", "type", "action", "path",
                             "model", "outcome", "status", "goal", "summary")
                }
                for e in chunk.events[:self.chunk_size]
            ],
            ensure_ascii=False,
        )
        prompt = (
            "You are a concise AI archivist. "
            "In ONE sentence, describe what the following swarm agent events accomplished "
            "or attempted. Focus on the goal, the model used, and the outcome.\n\n"
            f"Events:\n{snippet}\n\nOne-sentence summary:"
        )
        try:
            resp = await self._http.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model":  SUMMARISER_MODEL,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as exc:
            logger.warning("Summariser failed (%s); using rule-based fallback.", exc)
            types   = list(dict.fromkeys(chunk.event_types))[:3]
            models_ = list(dict.fromkeys(m for m in chunk.model_tags if m))[:2]
            return (
                f"Agent performed {', '.join(types) or 'unknown'} operations "
                f"using {', '.join(models_) or 'unknown model'}."
            )

    async def _flush_chunk(self) -> bool:
        """Summarise, embed, and upsert the active chunk. Returns True on success."""
        chunk = self._active_chunk
        if not chunk.events:
            return False

        summary = await self._summarise(chunk)
        if not summary:
            return False

        try:
            vector = self.emb.embed(summary)
        except Exception as exc:
            logger.error("Embedding failed for summary '%s': %s", summary[:80], exc)
            return False

        # Determine dominant outcome for the chunk
        failures  = sum(1 for o in chunk.outcomes if "fail" in o.lower() or "error" in o.lower())
        successes = len(chunk.outcomes) - failures
        dominant  = "failure" if failures > successes else "success"

        payload = {
            "summary":          summary,
            "event_types":      list(dict.fromkeys(chunk.event_types)),
            "models":           list(dict.fromkeys(m for m in chunk.model_tags if m)),
            "dominant_outcome": dominant,
            "failure_count":    failures,
            "success_count":    successes,
            "event_count":      len(chunk.events),
            "indexed_at":       time.time(),
            "source":           "memory_bridge",
        }

        point_id = str(uuid.uuid4())
        try:
            self.vs.upsert(doc_id=point_id, vector=vector, payload=payload)
            logger.info(
                "MemoryBridge: indexed memory point %s | outcome=%s | summary='%s...'",
                point_id[:8], dominant, summary[:72],
            )
            return True
        except Exception as exc:
            logger.error("VectorStore upsert failed: %s", exc)
            return False
