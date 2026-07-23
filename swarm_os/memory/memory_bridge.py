from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import networkx as nx

from swarm_os.services.embedding_service import EmbeddingService
from swarm_os.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# ============================================================
# MEMORY BRIDGE v12 (FINAL SWARM CORE)
# ============================================================

CHUNK_SIZE = 12
SESSION_WINDOW = 40
DEDUP_WINDOW = 300

OLLAMA = "http://localhost:11434"
SUM_MODEL = "phi4-mini:latest"
EMBED_MODEL = "nomic-embed-text:latest"
VECTOR_SIZE = 768

WATERMARK = Path("logs/.memory_bridge_offset.json")
STATE = Path("logs/.memory_bridge_state.json")

DECAY = 180.0

FLUSH_TRIGGERS = {"TASK_COMPLETE", "record_failure", "AGENT_ERROR"}
EVENT_TYPE_KEYS = ("event_type", "type", "action", "kind")


@dataclass
class Session:
    id: str
    events: List[Dict[str, Any]] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    outcomes: List[str] = field(default_factory=list)
    types: List[str] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)


@dataclass
class Bias:
    model: str
    event_type: str
    failure_rate: float
    confidence: float
    weight: float


class MemoryBridge:
    def __init__(
        self,
        event_log_path: Path | str = Path("logs/event_log.jsonl"),
        *,
        vector_store: Optional[VectorStore] = None,
        embedding_svc: Optional[EmbeddingService] = None,
    ) -> None:
        self.path = Path(event_log_path)

        self.vs = vector_store or VectorStore(
            collection_name="swarm_memory",
            vector_size=VECTOR_SIZE,
            use_memory=False,
        )

        self.emb = embedding_svc or EmbeddingService(
            base_url=OLLAMA,
            model=EMBED_MODEL,
        )

        self.http = httpx.AsyncClient(timeout=120.0)

        self.offset = self._load_offset()
        self.state = self._load_state()

        self.graph_path = Path("logs/memory_graph.graphml")
        self.graph = nx.DiGraph()
        self._load_graph()

        self.session = Session(id=str(uuid.uuid4()))

        self.lock_embed = asyncio.Lock()
        self.lock_vector = asyncio.Lock()
        self.graph_lock = asyncio.Lock()

        self.recent_hashes: deque[str] = deque(maxlen=DEDUP_WINDOW)
        self.embedding_cache: Dict[str, list] = {}

        self.policy: Dict[str, Dict[str, float]] = self.state

    async def ingest(self, flush_tail: bool = False) -> int:
        events, new_offset = self._read()

        if not events:
            if flush_tail and self.session.events:
                ok = await self._flush()
                self._reset()
                self._save_state()
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
        self._save_offset()
        self._save_state()

        return flushed

    async def watch_loop(self, interval_seconds: float = 5.0, flush_tail: bool = False) -> None:
        # BUG FIX: Actually launch the memory manager daemon as a background task.
        # Previously start_manager_daemon was defined but never scheduled, so archival never ran.
        daemon_task = asyncio.create_task(self.start_manager_daemon())
        try:
            while True:
                try:
                    await self.ingest(flush_tail=flush_tail)
                except Exception as exc:
                    logger.warning("ingest error: %s", exc)
                await asyncio.sleep(interval_seconds)
        finally:
            daemon_task.cancel()
            try:
                await daemon_task
            except asyncio.CancelledError:
                pass
            await self.close()
            
    async def start_manager_daemon(self, interval_seconds: float = 300.0) -> None:
        """Memory Manager Daemon that actively synthesizes core memory blocks and pages out to Archival Qdrant."""
        try:
            while True:
                try:
                    # Page out memory
                    consolidated = await self.consolidate_memories()
                    if consolidated:
                        logger.info("Memory Manager Daemon: Successfully synthesized core memory and paged raw logs to Archival Memory (Qdrant).")
                    
                    # Update graph clusters
                    await self.cluster_graph_rag()
                except Exception as exc:
                    logger.warning("manager daemon error: %s", exc)
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            pass

    def _add(self, event: Dict[str, Any]) -> None:
        model = str(event.get("model") or event.get("assigned_to") or "unknown")
        outcome = str(event.get("outcome") or event.get("status") or "")
        et = self._extract_event_type(event)
        task = str(event.get("task_id") or event.get("goal_id") or "")

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
        # BUG FIX: Cast timestamp to str for safe GraphML serialization.
        # NetworkX's GraphML writer can fail or silently corrupt float attributes
        # on strict parsers. String serialization is universally safe.
        ts = str(now)
        async def _mutate_graph():
            async with self.graph_lock:
                self.graph.add_node(session_node, type="Session")
                self.graph.add_node(agent_node, type="Agent")
                self.graph.add_node(task_node, type="Task")
                self.graph.add_node(tool_node, type="Tool")
                self.graph.add_node(outcome_node, type="Outcome")

                self.graph.add_edge(agent_node, session_node, relation="PARTICIPATED_IN")
                self.graph.add_edge(session_node, task_node, relation="ADDRESSED")
                self.graph.add_edge(task_node, tool_node, relation="UTILIZED")
                self.graph.add_edge(task_node, outcome_node, relation="RESULTED_IN")

        asyncio.create_task(_mutate_graph())

        # Epistemic Logic (Theory of Mind)
        details = str(event.get("details", "")).lower()
        if "found" in details or "discovered" in details or "learned" in details:
            fact_node = f"Fact_{hashlib.md5(details.encode()).hexdigest()[:8]}"
            self.graph.add_node(fact_node, type="Fact", content=details)
            self.graph.add_edge(agent_node, fact_node, relation="BELIEVES", timestamp=ts)

        delegated_to = event.get("delegated_to")
        if delegated_to:
            receiver_node = f"Agent_{delegated_to}"
            self.graph.add_node(receiver_node, type="Agent")
            self.graph.add_edge(receiver_node, agent_node, relation="KNOWS", timestamp=ts)

        self._update_policy(model, et, outcome)

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

        query = f"routing decision"
        results: List[Dict[str, Any]] = []

        try:
            vec = await self._embed(query)
            if vec is not None:
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                async with self.lock_vector:
                    results = await asyncio.to_thread(
                        self.vs.search,
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

        summary = await self._summarize()
        if not summary:
            return False

        vec = await self._embed(summary)
        if vec is None:
            return False

        failure_rate = self._failure_rate()

        payload = {
            "session": self.session.id,
            "summary": summary,
            "models": list(dict.fromkeys(self.session.models)),
            "types": list(dict.fromkeys(self.session.types)),
            "tasks": list(dict.fromkeys(self.session.tasks)),
            "event_count": len(self.session.events),
            "failure_rate": round(failure_rate, 3),
            "dominant_outcome": "failure" if failure_rate > 0.5 else "success",
            "source": "memory_bridge_v12",
            "indexed_at": time.time(),
        }

        self._save_graph()
        return await self._store(vec, payload)

    async def _store(self, vec: list, payload: dict) -> bool:
        try:
            async with self.lock_vector:
                await asyncio.to_thread(
                    self.vs.upsert,
                    doc_id=str(uuid.uuid4()),
                    vector=vec,
                    payload=payload,
                )
            return True
        except Exception as exc:
            logger.warning("vector store error: %s", exc)
            return False

    async def _embed(self, text: str) -> Optional[list]:
        # Fast path: no lock needed for read
        if text in self.embedding_cache:
            return self.embedding_cache[text]

        try:
            async with self.lock_embed:
                # BUG FIX: Double-checked locking pattern.
                # Multiple coroutines could be waiting on this lock for the same text.
                # Without the inner check, they would all re-embed sequentially and waste time.
                if text in self.embedding_cache:
                    return self.embedding_cache[text]
                vec = await asyncio.to_thread(self.emb.embed, text)
                self.embedding_cache[text] = vec
                return vec
        except Exception as exc:
            logger.warning("embedding error: %s", exc)
            return None

    async def _summarize(self) -> str:
        try:
            snippet = json.dumps(self.session.events[-CHUNK_SIZE:], ensure_ascii=False)
            response = await self.http.post(
                f"{OLLAMA}/api/generate",
                json={
                    "model": SUM_MODEL,
                    "prompt": f"Summarize in one sentence:\n{snippet}",
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
            response.raise_for_status()
            return (response.json().get("response") or "").strip()
        except Exception as exc:
            logger.warning("summarization error: %s", exc)
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
        except Exception:
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

    def _read(self) -> Tuple[List[Dict[str, Any]], int]:
        if not self.path.exists():
            return [], self.offset

        events: List[Dict[str, Any]] = []
        count = 0

        with self.path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                count = i + 1
                if i < self.offset:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue

        if self.offset > count:
            self.offset = 0
            return self._read()

        return events, count

    def _reset(self) -> None:
        self.session = Session(id=str(uuid.uuid4()))

    def _load_offset(self) -> int:
        try:
            return json.loads(WATERMARK.read_text(encoding="utf-8")).get("offset", 0)
        except Exception:
            return 0

    def _save_offset(self) -> None:
        WATERMARK.parent.mkdir(parents=True, exist_ok=True)
        WATERMARK.write_text(
            json.dumps({"offset": self.offset}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_state(self) -> Dict[str, Any]:
        try:
            data = json.loads(STATE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_state(self) -> None:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_graph(self) -> None:
        try:
            if self.graph_path.exists():
                self.graph = nx.read_graphml(self.graph_path)
        except Exception as exc:
            logger.warning("Failed to load graph: %s", exc)
            self.graph = nx.DiGraph()

    def _save_graph(self) -> None:
        try:
            self.graph_path.parent.mkdir(parents=True, exist_ok=True)
            nx.write_graphml(self.graph, self.graph_path)
        except Exception as exc:
            logger.warning("Failed to save graph: %s", exc)

    async def get_memory_context(self, query: str) -> str:
        """
        Retrieves similar memories and applies keyword boosting (hybrid search).
        """
        try:
            vec = await self._embed(query)
            if vec is None:
                return ""

            async with self.lock_vector:
                results = await asyncio.to_thread(
                    self.vs.search,
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
                
                # Check for exact matches of key task words
                boost = 0.0
                summary_words = set(summary.lower().split())
                matching_words = query_words & summary_words
                if matching_words:
                    boost += len(matching_words) * 0.05
                
                # Boost if query mentions a specific outcome or model
                outcome = payload.get("dominant_outcome", "").lower()
                if outcome and outcome in query.lower():
                    boost += 0.1
                
                score = hit.get("score", 0.0) + boost
                boosted_results.append((score, hit))

            # Re-sort by boosted score and limit to top 3
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
                
                # Hybrid Graph Traversal
                if session_id:
                    session_node = f"Session_{session_id}"
                    if self.graph.has_node(session_node):
                        edges = nx.edge_bfs(self.graph, session_node, orientation='original')
                        paths = []
                        pageranks = nx.get_node_attributes(self.graph, 'pagerank')
                        for u, v, _ in list(edges)[:15]:
                            rel = self.graph[u][v].get('relation', 'CONNECTED_TO')
                            score_u = pageranks.get(u, 0.0)
                            score_v = pageranks.get(v, 0.0)
                            if score_u > 0.01 or score_v > 0.01 or "Community" in v or "Community" in u:
                                paths.append(f"  * {u} -> {rel} -> {v} [Importance: {score_v:.4f}]")
                            else:
                                paths.append(f"  * {u} -> {rel} -> {v}")
                        if paths:
                            context_parts.append("  [GraphRAG Subgraph Context]:")
                            # Deduplicate and sort by length/importance (simplified)
                            context_parts.extend(list(dict.fromkeys(paths))[:10])
            
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
            # Retrieve up to 100 entries from Qdrant that are not consolidated
            async with self.lock_vector:
                records, _ = await asyncio.to_thread(
                    self.vs.client.scroll,
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

            # Group them by dominant_outcome
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
                        f"{OLLAMA}/api/generate",
                        json={
                            "model": SUM_MODEL,
                            "prompt": prompt,
                            "stream": False,
                            "options": {"temperature": 0},
                        },
                    )
                    response.raise_for_status()
                    new_summary = (response.json().get("response") or "").strip()
                except Exception:
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
                        await asyncio.to_thread(
                            self.vs.client.delete,
                            collection_name=self.vs.collection_name,
                            points_selector=PointIdsList(points=point_ids)
                        )
                    consolidated_any = True

            return consolidated_any
        except Exception as exc:
            logger.warning("Memory consolidation failed: %s", exc)
            return False

    async def cluster_graph_rag(self) -> None:
        """
        Phase 4: GraphRAG Advanced Extraction.
        Uses Louvain Community Detection to group related sessions and agents.
        Calculates PageRank to highlight the most structurally important elements.
        Generates hierarchical summaries for each community.
        """
        try:
            from networkx.algorithms import community
            if len(self.graph.nodes) < 5:
                return

            undirected_graph = self.graph.to_undirected()
            communities = community.louvain_communities(undirected_graph)
            
            pagerank_scores = nx.pagerank(self.graph)
            nx.set_node_attributes(self.graph, pagerank_scores, 'pagerank')

            for idx, c in enumerate(communities):
                if len(c) < 3:
                    continue
                comm_node = f"Community_Cluster_{idx}"
                async with self.graph_lock:
                    if self.graph.has_node(comm_node):
                        continue
                        
                    self.graph.add_node(comm_node, type="Community")
                    
                    for node in c:
                        self.graph.add_edge(node, comm_node, relation="BELONGS_TO")

                important_nodes = sorted([n for n in c], key=lambda x: pagerank_scores.get(x, 0), reverse=True)[:5]
                prompt = f"Summarize the relationship between these graph nodes which belong to the same community cluster: {', '.join(important_nodes)}"
                
                try:
                    response = await self.http.post(
                        f"{OLLAMA}/api/generate",
                        json={
                            "model": SUM_MODEL,
                            "prompt": prompt,
                            "stream": False,
                            "options": {"temperature": 0},
                        },
                    )
                    response.raise_for_status()
                    summary = (response.json().get("response") or "").strip()
                except Exception:
                    summary = f"Community cluster {idx} containing {len(c)} nodes."
                
                nx.set_node_attributes(self.graph, {comm_node: summary}, 'summary')
                
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
            
            self._save_graph()
            logger.info(f"GraphRAG: Found {len(communities)} communities and computed PageRank.")
        except Exception as exc:
            logger.warning("GraphRAG clustering failed: %s", exc)

    async def close(self) -> None:
        self._save_state()
        await self.http.aclose()

