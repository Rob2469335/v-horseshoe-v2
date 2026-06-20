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

from swarm_os.services.embedding_service import EmbeddingService
from swarm_os.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# ============================================================
# MEMORY BRIDGE v12 (FINAL SWARM CORE)
# ============================================================

COLLECTION = "horseshoe_swarm_memory_final_v12"

CHUNK_SIZE = 12
SESSION_WINDOW = 40
DEDUP_WINDOW = 300

OLLAMA = "http://localhost:11434"
SUM_MODEL = "mistral-nemo"
EMBED_MODEL = "nomic-embed-text"
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
            collection_name=COLLECTION,
            vector_size=VECTOR_SIZE,
            use_memory=False,
        )

        self.emb = embedding_svc or EmbeddingService(
            base_url=OLLAMA,
            model=EMBED_MODEL,
        )

        self.http = httpx.AsyncClient(timeout=60.0)

        self.offset = self._load_offset()
        self.state = self._load_state()

        self.session = Session(id=str(uuid.uuid4()))

        self.lock_embed = asyncio.Lock()
        self.lock_vector = asyncio.Lock()

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
        try:
            while True:
                try:
                    await self.ingest(flush_tail=flush_tail)
                except Exception as exc:
                    logger.warning("ingest error: %s", exc)
                await asyncio.sleep(interval_seconds)
        finally:
            await self.close()

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

        query = f"event_type:{event_type} model:{model} routing decision"
        results: List[Dict[str, Any]] = []

        try:
            vec = await self._embed(query)
            if vec is not None:
                async with self.lock_vector:
                    results = await asyncio.to_thread(
                        self.vs.search,
                        query_vector=vec,
                        limit=top_k,
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
        if text in self.embedding_cache:
            return self.embedding_cache[text]

        try:
            async with self.lock_embed:
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

    async def close(self) -> None:
        self._save_state()
        await self.http.aclose()
