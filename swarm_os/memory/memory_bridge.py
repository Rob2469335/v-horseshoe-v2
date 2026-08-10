from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from swarm_os.services.embedding_service import EmbeddingService
from swarm_os.services.vector_store import VectorStore
from swarm_os.repositories.event_log_repo import EventLogRepository
from swarm_os.repositories.graph_repo import GraphRepository
from swarm_os.services.memory_daemon import MemoryDaemon
from swarm_os.memory._memory_bridge_base import (
    CHUNK_SIZE, SESSION_WINDOW, DEDUP_WINDOW,
    LLAMA_EMB, LLAMA_SUMM, SUM_MODEL, EMBED_MODEL, VECTOR_SIZE,
    DECAY, FLUSH_TRIGGERS, EVENT_TYPE_KEYS,
    Session, Bias,
)

logger = logging.getLogger(__name__)


class MemoryBridge:
    def __init__(
        self,
        event_log_path: Path | str = Path("logs/event_log.jsonl"),
        *,
        vector_store: Optional[VectorStore] = None,
        embedding_svc: Optional[EmbeddingService] = None,
    ) -> None:
        self.event_repo = EventLogRepository(
            event_log_path=event_log_path,
            watermark_path="logs/.memory_bridge_offset.json",
            state_path="logs/.memory_bridge_state.json"
        )
        self.graph_repo = GraphRepository(
            graph_path="logs/memory_graph.graphml"
        )

        self.vs = vector_store or VectorStore(
            collection_name="swarm_memory",
            vector_size=VECTOR_SIZE,
            use_memory=False,
        )

        self.emb = embedding_svc or EmbeddingService(
            base_url=LLAMA_EMB,
            model=EMBED_MODEL,
        )

        self.http = httpx.AsyncClient(timeout=120.0, headers={"Authorization": "Bearer llama"})

        self.offset = self.event_repo.load_offset()
        self.state = self.event_repo.load_state()

        self.session = Session(id=str(uuid.uuid4()))

        self.lock_embed = asyncio.Lock()
        self.lock_vector = asyncio.Lock()

        self.recent_hashes: deque[str] = deque(maxlen=DEDUP_WINDOW)
        # UPGRADE: track background graph tasks so they aren't GC'd mid-flight and
        # surface exceptions instead of silently vanishing (silent memory data loss).
        self._bg_tasks: set[asyncio.Task] = set()
        from collections import OrderedDict
        class LRUCache(OrderedDict):
            def __init__(self, maxsize=1000, *args, **kwds):
                self.maxsize = maxsize
                super().__init__(*args, **kwds)
            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                if len(self) > self.maxsize:
                    oldest = next(iter(self))
                    del self[oldest]
                    
        self.embedding_cache: Dict[str, list] = LRUCache(maxsize=1000)

        self.policy: Dict[str, Dict[str, float]] = self.state

    async def ingest(self, flush_tail: bool = False) -> int:
        # Bounded tail read: a fresh/rotated journal (offset 0) could otherwise
        # load the WHOLE events.jsonl into memory on every boot. Cap to the most
        # recent events; the offset still advances past everything so nothing is
        # re-read, and memory stays bounded.
        events, new_offset = await asyncio.to_thread(self.event_repo.read_events, self.offset, 5000)

        if not events:
            if flush_tail and self.session.events:
                ok = await self._flush()
                if ok:
                    self._reset()
                await asyncio.to_thread(self.event_repo.save_state, self.state)
                return 1 if ok else 0
            return 0

        flushed = 0

        for event in events:
            if self._is_duplicate(event):
                continue

            self._add(event)

            if self._should_flush(event):
                if await self._flush():
                    flushed += 1
                    self._reset()

        if flush_tail and self.session.events:
            if await self._flush():
                flushed += 1
                self._reset()

        self.offset = new_offset
        await asyncio.to_thread(self.event_repo.save_offset, self.offset)
        await asyncio.to_thread(self.event_repo.save_state, self.state)

        return flushed

    async def watch_loop(self, interval_seconds: float = 5.0, flush_tail: bool = False) -> None:
        # BUG FIX: Removed internal start_manager_daemon() spawn here — main.py already
        # starts the consolidation daemon explicitly. Two daemons caused duplicate
        # consolidated points and double LLM summarization cost.
        try:
            while True:
                try:
                    await self.ingest(flush_tail=flush_tail)
                except Exception as exc:
                    logger.warning("ingest error: %s", exc)
                await asyncio.sleep(interval_seconds)
        finally:
            await self.close()
            
    async def start_manager_daemon(self, interval_seconds: float = 300.0) -> None:
        daemon = MemoryDaemon(self, interval_seconds)
        await daemon.start()
            
    def _add(self, event: Dict[str, Any]) -> None:
        # BUG FIX: Events written by agent_service_v2 use EventEnvelope, which nests
        # model/task_id/outcome/status inside `payload`. Reading top-level keys only
        # produced model="unknown", outcome="" and task="" for every event, so Qdrant
        # entries carried no usable metadata and the swarm memory stayed empty.
        pl = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        model = str(event.get("model") or pl.get("model") or event.get("assigned_to") or pl.get("assigned_to") or "unknown")
        outcome = str(event.get("outcome") or pl.get("outcome") or event.get("status") or pl.get("status") or "")
        et = self._extract_event_type(event)
        task = str(event.get("task_id") or pl.get("task_id") or event.get("goal_id") or pl.get("goal_id") or "")

        self.session.events.append(event)
        self.session.models.append(model)
        self.session.outcomes.append(outcome)
        self.session.types.append(et)
        self.session.tasks.append(task)

        # Graph Ontology Extraction
        session_node = f"Session_{self.session.id}"
        agent_node = f"Agent_{model}"
        task_node = f"Task_{task}" if task else "Task_unknown"
        tool_node = f"Tool_{et}"
        outcome_node = f"Outcome_{outcome}" if outcome else "Outcome_unknown"
        
        now = time.time()
        ts = str(now)
        
        self._spawn(self.graph_repo.add_session_data(
            session_node, agent_node, task_node, tool_node, outcome_node
        ))

        # Epistemic Logic (Theory of Mind)
        details = str(event.get("details", "")).lower()
        if "found" in details or "discovered" in details or "learned" in details:
            fact_node = f"Fact_{hashlib.sha256(details.encode()).hexdigest()[:8]}"
            self._spawn(self.graph_repo.add_fact(
                fact_node, agent_node, details, ts
            ))

        delegated_to = event.get("delegated_to")
        if delegated_to:
            receiver_node = f"Agent_{delegated_to}"
            self._spawn(self.graph_repo.add_delegation(
                receiver_node, agent_node, ts
            ))

        self._update_policy(model, et, outcome)

    def _spawn(self, coro) -> asyncio.Task | None:
        """Spawn a background task with a strong reference + error observer.

        Without the strong reference, `asyncio.create_task` tasks can be
        garbage-collected mid-await and their exceptions silently swallowed —
        which shows up as 'memories silently not persisting'."""
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            return None
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return task

    def _update_policy(self, model: str, et: str, outcome: str) -> None:
        key = f"{model}:{et}"
        state = self.policy.setdefault(
            key,
            {"fail": 0.0, "success": 0.0, "weight": 1.0, "confidence": 0.0, "last": time.time()},
        )

        now = time.time()
        last_seen = float(state.get("last", now))
        decay = math.exp(-(now - last_seen) / DECAY) if DECAY > 0 else 1.0

        state["fail"] = float(state.get("fail", 0.0)) * decay
        state["success"] = float(state.get("success", 0.0)) * decay

        lowered = outcome.lower()
        if "fail" in lowered or "error" in lowered:
            state["fail"] += 1.0
        else:
            state["success"] += 1.0

        total = state["fail"] + state["success"]
        rate = state["fail"] / total if total else 0.0

        state["weight"] = max(0.05, 1.0 - rate)
        state["confidence"] = min(1.0, math.log1p(total) / 5.0)
        state["last"] = now

    def get_bias(self, model: str, et: str) -> Bias:
        s = self.policy.get(f"{model}:{et}", {})
        fail = float(s.get("fail", 0.0))
        success = float(s.get("success", 0.0))
        total = fail + success

        rate = fail / total if total else 0.0
        conf = float(s.get("confidence", min(1.0, math.log1p(total) / 5.0) if total else 0.0))
        weight = float(s.get("weight", 1.0))

        return Bias(
            model=model,
            event_type=et,
            failure_rate=rate,
            confidence=conf,
            weight=weight,
        )

    def routing_signal(self, model: str, et: str) -> Dict[str, Any]:
        b = self.get_bias(model, et)
        return {
            "model": model,
            "event_type": et,
            "weight": round(b.weight, 3),
            "failure_rate": round(b.failure_rate, 3),
            "confidence": round(b.confidence, 3),
            "decision": "avoid" if b.weight < 0.4 else "ok",
        }

    async def query_routing_hint(
        self,
        event_type: str,
        model: str,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        local = self.routing_signal(model, event_type)

        query = "routing decision"
        results: List[Dict[str, Any]] = []

        try:
            vec = await self._embed(query)
            if vec is not None:
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                async with self.lock_vector:
                    results = await self.vs.search(
                        query_vector=vec,
                        limit=top_k,
                        filter_condition=Filter(
                            must=[
                                FieldCondition(key="types", match=MatchValue(value=event_type)),
                                FieldCondition(key="models", match=MatchValue(value=model))
                            ]
                        )
                    )
        except Exception as exc:
            logger.warning("routing hint search error: %s", exc)

        model_scores: Dict[str, float] = {}
        for result in results:
            payload = result.get("payload", {}) or {}
            for candidate in payload.get("models", []) or []:
                if candidate and candidate != model:
                    fr = float(payload.get("failure_rate", 0.5))
                    model_scores[candidate] = model_scores.get(candidate, 0.0) + (1.0 - fr)

        best_alt = max(model_scores, key=lambda m: model_scores[m], default="")

        return {
            "model": model,
            "event_type": event_type,
            "weight": local["weight"],
            "failure_rate": local["failure_rate"],
            "confidence": local["confidence"],
            "suggest_avoid": local["decision"] == "avoid",
            "suggested_prefer": best_alt,
            "evidence_count": len(results),
        }

    async def _flush(self) -> bool:
        if not self.session.events:
            return False

        failure_rate = self._failure_rate()
        stored = 0

        # BUG/SCALE FIX: store EACH event as its own vector memory point (no LLM
        # round-trip per event). Previously the whole session was collapsed into ONE
        # summary point behind a serial _summarize() LLM call — so ~12 events became
        # 1 point and throughput was LLM-bound. Now every event is individually
        # embedded and searchable, letting the store grow toward tens of thousands.
        for event in self.session.events:
            try:
                text = self._event_text(event)
                if not text:
                    continue
                vec = await self._embed(text)
                if vec is None:
                    continue
                pl = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                payload = {
                    "session": self.session.id,
                    "text": text,
                    "event_type": self._extract_event_type(event),
                    "model": str(event.get("model") or pl.get("model") or self.session.models[-1] if self.session.models else "unknown"),
                    "task_id": str(event.get("task_id") or pl.get("task_id") or ""),
                    "outcome": str(event.get("outcome") or pl.get("outcome") or event.get("status") or pl.get("status") or ""),
                    "failure_rate": round(failure_rate, 3),
                    "dominant_outcome": "failure" if failure_rate > 0.5 else "success",
                    "source": "memory_bridge_v12",
                    "indexed_at": time.time(),
                }
                if await self._store(vec, payload):
                    stored += 1
            except Exception as exc:
                logger.warning("event store error: %s", exc)

        # Keep the session-level summary point too (high-level context + policy stats)
        summary = await self._summarize()
        if summary:
            vec = await self._embed(summary)
            if vec:
                await self._store(vec, {
                    "session": self.session.id,
                    "summary": summary,
                    "models": list(dict.fromkeys(self.session.models)),
                    "types": list(dict.fromkeys(self.session.types)),
                    "tasks": list(dict.fromkeys(self.session.tasks)),
                    "event_count": len(self.session.events),
                    "failure_rate": round(failure_rate, 3),
                    "dominant_outcome": "failure" if failure_rate > 0.5 else "success",
                    "source": "memory_bridge_v12_summary",
                    "indexed_at": time.time(),
                })

        await self.graph_repo.save()
        return stored > 0

    def _event_text(self, event: Dict[str, Any]) -> str:
        """Derive a searchable text string from an event for embedding."""
        pl = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        et = self._extract_event_type(event)
        # Prefer meaningful payload content; fall back to action/result summaries.
        content = pl.get("content") or pl.get("result") or pl.get("summary") or ""
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)[:500]
        parts = [et]
        if content:
            parts.append(str(content)[:500])
        action = pl.get("action") or event.get("action")
        if action:
            parts.append(f"action={action}")
        outcome = pl.get("outcome") or pl.get("status") or event.get("outcome") or event.get("status")
        if outcome:
            parts.append(f"outcome={outcome}")
        text = " | ".join(p for p in parts if p)
        return text[:600]

    async def _store(self, vec: list, payload: dict) -> bool:
        try:
            async with self.lock_vector:
                await self.vs.upsert(
                    doc_id=str(uuid.uuid4()),
                    vector=vec,
                    payload=payload,
                )
            return True
        except Exception as exc:
            logger.exception("vector store error: %s", exc)
            return False

    async def _embed(self, text: str) -> Optional[list]:
        if text in self.embedding_cache:
            return self.embedding_cache[text]

        try:
            async with self.lock_embed:
                if text in self.embedding_cache:
                    return self.embedding_cache[text]
                vec = await self.emb.embed(text)
                self.embedding_cache[text] = vec
                return vec
        except Exception as exc:
            logger.warning("embedding error: %s", exc)
            return None

    async def _summarize(self) -> str:
        try:
            snippet = json.dumps(self.session.events[-CHUNK_SIZE:], ensure_ascii=False)
            if self.http.is_closed:
                self.http = httpx.AsyncClient(timeout=120.0, headers={"Authorization": "Bearer llama"})
            response = await self.http.post(
                f"{LLAMA_SUMM}/v1/chat/completions",
                json={
                    "model": SUM_MODEL,
                    "messages": [{"role": "user", "content": f"Summarize in one sentence:\n{snippet}"}],
                    "stream": False,
                    "temperature": 0.0,
                    "max_tokens": 500,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            return (response.json()["choices"][0]["message"]["content"] or "").strip()
        except (httpx.ReadError, httpx.ReadTimeout) as exc:
            # BUG FIX: the single llama.cpp slot is often busy with a main-agent
            # generation when _summarize fires. ReadError/ReadTimeout here just means
            # the slot was occupied — log at debug (not warning) and fall back.
            logger.debug("summarization skipped (slot busy): %r", exc)
            types = list(dict.fromkeys(self.session.types))[:3]
            models = list(dict.fromkeys(m for m in self.session.models if m))[:2]
            return f"Agent performed {', '.join(types) or 'unknown'} using {', '.join(models) or 'unknown model'}."
        except Exception as exc:
            logger.exception("summarization error: %r", exc)
            types = list(dict.fromkeys(self.session.types))[:3]
            models = list(dict.fromkeys(m for m in self.session.models if m))[:2]
            return f"Agent performed {', '.join(types) or 'unknown'} using {', '.join(models) or 'unknown model'}."

    def _failure_rate(self) -> float:
        if not self.session.outcomes:
            return 0.0

        failures = sum(
            1
            for outcome in self.session.outcomes
            if "fail" in outcome.lower() or "error" in outcome.lower()
        )
        return failures / len(self.session.outcomes)

    def _should_flush(self, event: Dict[str, Any]) -> bool:
        et = self._extract_event_type(event)
        return (
            len(self.session.events) >= CHUNK_SIZE
            or len(self.session.events) >= SESSION_WINDOW
            or et in FLUSH_TRIGGERS
        )

    def _is_duplicate(self, event: Dict[str, Any]) -> bool:
        try:
            raw = json.dumps(
                event,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        except Exception as exc:
            # BUG FIX: was silently swallowing JSON/hash errors with no log.
            logger.debug("duplicate check error (treating as non-duplicate): %s", exc)
            return False

        if h in self.recent_hashes:
            return True

        self.recent_hashes.append(h)
        return False

    @staticmethod
    def _extract_event_type(event: Dict[str, Any]) -> str:
        for k in EVENT_TYPE_KEYS:
            if event.get(k):
                return str(event[k])
        return "UNKNOWN"

    def _reset(self) -> None:
        self.session = Session(id=str(uuid.uuid4()))

    async def get_memory_context(self, query: str) -> str:
        """
        Retrieves similar memories and applies keyword boosting (hybrid search).
        """
        try:
            vec = await self._embed(query)
            if vec is None:
                return ""

            async with self.lock_vector:
                results = await self.vs.search(
                    query_vector=vec,
                    limit=5,
                )

            if not results:
                return ""

            # Keyword Boosting (lexical relevance)
            query_words = set(query.lower().split())
            boosted_results = []
            for hit in results:
                payload = hit.get("payload", {}) or {}
                summary = payload.get("summary", "")
                
                boost = 0.0
                summary_words = set(summary.lower().split())
                matching_words = query_words & summary_words
                if matching_words:
                    boost += len(matching_words) * 0.05
                
                outcome = payload.get("dominant_outcome", "").lower()
                if outcome and outcome in query.lower():
                    boost += 0.1
                
                score = hit.get("score", 0.0) + boost
                boosted_results.append((score, hit))

            boosted_results.sort(key=lambda x: x[0], reverse=True)
            top_hits = [item[1] for item in boosted_results[:3]]

            context_parts = ["### Relevant historical context from swarm runs:"]
            for hit in top_hits:
                payload = hit.get("payload", {}) or {}
                summary = payload.get("summary", "")
                models = payload.get("models", [])
                outcome = payload.get("dominant_outcome", "unknown")
                session_id = payload.get("session", "")
                
                if summary:
                    context_parts.append(f"- Summary: {summary} (Models: {', '.join(models)}, Outcome: {outcome})")
                
                if session_id:
                    session_node = f"Session_{session_id}"
                    paths = await asyncio.to_thread(self.graph_repo.get_session_paths, session_node, limit=15)
                    paths_str = []
                    for u, v, rel, score_u, score_v in paths:
                        if score_u > 0.01 or score_v > 0.01 or "Community" in v or "Community" in u:
                            paths_str.append(f"  * {u} -> {rel} -> {v} [Importance: {score_v:.4f}]")
                        else:
                            paths_str.append(f"  * {u} -> {rel} -> {v}")
                    if paths_str:
                        context_parts.append("  [GraphRAG Subgraph Context]:")
                        context_parts.extend(list(dict.fromkeys(paths_str))[:10])
            
            return "\n".join(context_parts) + "\n"
        except Exception as e:
            logger.warning("Failed to retrieve memory context: %s", e)
            return ""

    async def consolidate_memories(self) -> bool:
        """
        Periodically retrieve all memory nodes, summarize groups of related entries,
        and upsert a unified consolidated summary while deleting the old individual entries.
        """
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            async with self.lock_vector:
                records, _ = await self.vs.client.scroll(
                    collection_name=self.vs.collection_name,
                    limit=100,
                    with_payload=True,
                    with_vectors=False,
                    scroll_filter=Filter(
                        must_not=[
                            FieldCondition(key="consolidated", match=MatchValue(value=True))
                        ]
                    )
                )
            if not records or len(records) < 3:
                return False

            groups: dict[str, list[Any]] = {}
            for r in records:
                payload = r.payload or {}
                if payload.get("consolidated"):
                    continue
                outcome = payload.get("dominant_outcome", "unknown")
                groups.setdefault(outcome, []).append(r)

            consolidated_any = False
            for outcome, items in groups.items():
                if len(items) < 2:
                    continue

                summaries = [it.payload.get("summary", "") for it in items if it.payload.get("summary")]
                combined_text = " | ".join(summaries)
                
                prompt = f"Summarize these agent swarm run summaries into a single cohesive sentence summarizing the outcome '{outcome}':\n{combined_text}"
                
                try:
                    response = await self.http.post(
                        f"{LLAMA_SUMM}/v1/chat/completions",
                        json={
                            "model": SUM_MODEL,
                            "messages": [{"role": "user", "content": prompt}],
                            "stream": False,
                            "temperature": 0.0,
                            "max_tokens": 500,
                        },
                        timeout=60.0,
                    )
                    response.raise_for_status()
                    new_summary = (response.json()["choices"][0]["message"]["content"] or "").strip()
                except (httpx.ReadError, httpx.ReadTimeout) as exc:
                    # Single llama.cpp slot often busy with a main-agent generation.
                    # Slot-busy is expected, not an error — log at debug and fall back
                    # to a concatenated summary instead of a noisy traceback.
                    logger.debug("consolidation LLM skipped (slot busy) for outcome '%s': %r", outcome, exc)
                    new_summary = f"Consolidated summary for {len(items)} runs with outcome {outcome}: " + ", ".join(summaries[:3])
                except Exception as exc:
                    # BUG FIX: was silently swallowing LLM HTTP failures with no log,
                    # causing 'Memory consolidation failed:' with blank message.
                    logger.exception("consolidation LLM failed for outcome '%s': %s", outcome, exc)
                    new_summary = f"Consolidated summary for {len(items)} runs with outcome {outcome}: " + ", ".join(summaries[:3])

                vec = await self._embed(new_summary)
                if vec is None:
                    continue

                payload = {
                    "summary": new_summary,
                    "dominant_outcome": outcome,
                    "models": list(dict.fromkeys(sum([it.payload.get("models", []) for it in items], []))),
                    "types": list(dict.fromkeys(sum([it.payload.get("types", []) for it in items], []))),
                    "tasks": list(dict.fromkeys(sum([it.payload.get("tasks", []) for it in items], []))),
                    "event_count": sum([it.payload.get("event_count", 0) for it in items]),
                    "consolidated": True,
                    "source": "memory_bridge_consolidator",
                    "indexed_at": time.time(),
                }

                success = await self._store(vec, payload)
                if success:
                    from qdrant_client.models import PointIdsList
                    async with self.lock_vector:
                        point_ids = [it.id for it in items]
                        await self.vs.client.delete(
                            collection_name=self.vs.collection_name,
                            points_selector=PointIdsList(points=point_ids)
                        )
                    consolidated_any = True

            return consolidated_any
        except (httpx.ReadError, httpx.ReadTimeout) as exc:
            # Qdrant transport error (starting up / briefly unavailable) is
            # expected during the startup window and on a busy machine — log at
            # warning WITHOUT a full traceback and retry on the next daemon tick.
            logger.warning("Memory consolidation skipped (Qdrant transport): %r", exc)
            return False
        except Exception as exc:
            if "ResponseHandlingException" in type(exc).__name__ or "UnexpectedResponse" in type(exc).__name__:
                logger.warning("Memory consolidation skipped (Qdrant transport): %r", exc)
                return False
            logger.warning("Memory consolidation failed: %s", exc, exc_info=True)
            return False

    async def cluster_graph_rag(self) -> None:
        """
        Phase 4: GraphRAG Advanced Extraction.
        Uses Louvain Community Detection to group related sessions and agents.
        Calculates PageRank to highlight the most structurally important elements.
        Generates hierarchical summaries for each community.
        """
        try:
            if self.graph_repo.get_node_count() < 5:
                return

            communities = await asyncio.to_thread(self.graph_repo.get_communities)
            pagerank_scores = await asyncio.to_thread(self.graph_repo.compute_pageranks)

            for idx, c in enumerate(communities):
                if len(c) < 3:
                    continue
                comm_node = f"Community_Cluster_{idx}"
                
                await self.graph_repo.ensure_community_node(comm_node, c)

                important_nodes = sorted([n for n in c], key=lambda x: pagerank_scores.get(x, 0), reverse=True)[:5]
                prompt = f"Summarize the relationship between these graph nodes which belong to the same community cluster: {', '.join(important_nodes)}"
                
                try:
                    response = await self.http.post(
                        f"{LLAMA_SUMM}/v1/chat/completions",
                        json={
                            "model": SUM_MODEL,
                            "messages": [{"role": "user", "content": prompt}],
                            "stream": False,
                            "temperature": 0.0,
                            "max_tokens": 500,
                        },
                        timeout=60.0,
                    )
                    response.raise_for_status()
                    summary = (response.json()["choices"][0]["message"]["content"] or "").strip()
                except (httpx.ReadError, httpx.ReadTimeout) as exc:
                    # Single llama.cpp slot is frequently busy with a main-agent
                    # generation or consolidation when cluster_graph_rag fires.
                    # ReadError/ReadTimeout = slot occupied — log at debug and fall
                    # back to a structural summary instead of a noisy traceback.
                    logger.debug("graph_rag cluster LLM skipped (slot busy) for cluster %d: %r", idx, exc)
                    summary = f"Community cluster {idx} containing {len(c)} nodes."
                except Exception as exc:
                    # BUG FIX: was silently swallowing LLM HTTP failures with no log.
                    logger.exception("graph_rag cluster LLM failed for cluster %d: %s", idx, exc)
                    summary = f"Community cluster {idx} containing {len(c)} nodes."
                
                await self.graph_repo.set_community_summary(comm_node, summary)
                
                vec = await self._embed(summary)
                if vec:
                    payload = {
                        "summary": summary,
                        "dominant_outcome": "community_cluster",
                        "event_count": len(c),
                        "source": "graphrag_community",
                        "indexed_at": time.time(),
                        "cluster_id": idx
                    }
                    await self._store(vec, payload)
            
            await self.graph_repo.save()
            logger.info(f"GraphRAG: Found {len(communities)} communities and computed PageRank.")
        except (httpx.ReadError, httpx.ReadTimeout) as exc:
            logger.warning("GraphRAG clustering skipped (Qdrant/LLM transport): %r", exc)
        except Exception as exc:
            if "ResponseHandlingException" in type(exc).__name__ or "UnexpectedResponse" in type(exc).__name__:
                logger.warning("GraphRAG clustering skipped (Qdrant transport): %r", exc)
            else:
                logger.exception("GraphRAG clustering failed: %s", exc)

    async def close(self) -> None:
        # Cancel pending fire-and-forget graph tasks BEFORE tearing down clients
        # (the _spawn() done-callbacks discard from _bg_tasks as they finish, so
        # snapshot the set first). Without this, a spawned graph write could run
        # against a closed httpx/embedding client — and the interpreter would see
        # "Task was destroyed but it is pending" warnings on shutdown.
        pending = list(self._bg_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.to_thread(self.event_repo.save_state, self.state)
        await self.http.aclose()
        if self.emb is not None:
            try:
                await self.emb.aclose()
            except Exception as exc:
                logger.warning("Error closing embedding client: %s", exc)
