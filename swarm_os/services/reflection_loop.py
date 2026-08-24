import os
import json
import logging
import asyncio
import time
import uuid
import re
from pathlib import Path
from litellm import acompletion
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    PointStruct,
    VectorParams,
    Distance,
    Filter,
    FieldCondition,
    MatchValue,
)
from swarm_os.services.embedding_service import EmbeddingService

logger = logging.getLogger("ReflectionLoop")

_point_locks: dict[str, asyncio.Lock] = {}
_POINT_LOCK_MAX = 256


def _get_point_lock(point_id: str) -> asyncio.Lock:
    # setdefault inserts only if absent — atomic, no check-then-assign race
    # between two concurrent callers for the same point_id. Bounded: point ids
    # are deterministic (uuid5 of component|failure_reason), but prune when the
    # dict grows past _POINT_LOCK_MAX so it can never leak unbounded (a distinct
    # key per (component, failure_reason) combination).
    lock = _point_locks.setdefault(point_id, asyncio.Lock())
    if len(_point_locks) > _POINT_LOCK_MAX:
        # Evict old locks only if they are not actively held. If all 256 happen
        # to be held at once (burst updates), we safely skip eviction and allow
        # the dictionary to temporarily grow past 256 rather than risk a race.
        for k in list(_point_locks):
            if len(_point_locks) <= _POINT_LOCK_MAX:
                break
            if not _point_locks[k].locked():
                _point_locks.pop(k, None)
    return lock


ROOT_DIR = Path(__file__).parent.parent.parent.resolve()
DIARY_PATH = ROOT_DIR / "swarm_os" / "logs" / "organism_diary.jsonl"
LOCAL_MODEL = "qwen3.5-4b"  # Local llama.cpp alias
# Sanctioned free cloud model (DeepSeek V4 flash via the funded OpenCode Go
# account). Local qwen3.5-4b burns all max_tokens on reasoning_content for the
# long distiller prompt, producing empty content at ~5 tok/s; DeepSeek flash
# emits the structured reflection directly.
CLOUD_MODEL = "openai/deepseek-v4-flash"
DISTILLER_MAX_TOKENS_CLOUD = 300
DISTILLER_MAX_TOKENS_LOCAL = 512


def _corrections_similar(a: str, b: str, threshold: float = 0.5) -> bool:
    """Content-based test of whether two reflexion corrections say the SAME thing
    (2026 L5: judge repeat-vs-conflict on the fact's content, not on the key
    having been written before). Deterministic, no LLM.

    Normalize to lowercased alphanumeric words (dropping stopwords/short tokens),
    then compare token overlaps: two corrections are "the same fact" when at least
    `threshold` of the smaller set's meaningful words appears in the other. Either
    side empty is treated as same (no substance to contradict)."""
    import re as _re

    _STOP = {
        "the",
        "a",
        "an",
        "to",
        "of",
        "for",
        "on",
        "in",
        "with",
        "and",
        "or",
        "is",
        "are",
        "be",
        "do",
        "not",
        "you",
        "your",
        "it",
        "its",
        "use",
        "using",
        "file",
        "files",
        "first",
        "then",
        "before",
        "so",
    }
    # Negation is a STRONG conflict signal: "never web_search" vs "web_search for
    # every goal" share tokens but direct the agent opposite ways. If a negation
    # marker appears on exactly ONE side, treat as a conflict regardless of the
    # token overlap — the same precedence discipline as the L2 classifier (a
    # semantic opposite must never be reinforced as "the same fact").
    _NEG = re.compile(r"\b(never|don't|do not|not|without|avoid|must not|should not)\b")
    a_neg = bool(_NEG.search(str(a or "").lower()))
    b_neg = bool(_NEG.search(str(b or "").lower()))
    if a_neg != b_neg:
        return False  # one side forbids, the other commands -> conflicting

    def _words(s):
        return {
            w
            for w in _re.findall(r"[a-z0-9]+", str(s or "").lower())
            if len(w) > 2 and w not in _STOP
        }

    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return True  # nothing substantive -> ambiguous, treat as same (reinforce)
    overlap = len(wa & wb) / max(1.0, min(len(wa), len(wb)))
    return overlap >= threshold


_RULE_SAME = "same"
_RULE_CONFLICT = "conflict"


def _classify_rule(existing: dict | None, correction: str) -> str:
    """Classify a reflector write as same-fact (reinforce) or conflict (overwrite)
    based on the correction CONTENT (not the point existing). A new, materially
    different correction for the same failure_reason is a genuine conflict; an
    identical/rephrased one is the same fact recurring."""
    if not existing:
        return _RULE_SAME  # first write, just store it
    if _corrections_similar(existing.get("correction", ""), correction):
        return _RULE_SAME
    return _RULE_CONFLICT


def _record_rule_to_agents_md(component: str, correction: str, confidence: float):
    """SOTA 2026: Auto-document high-confidence ASPO reflection rules into AGENTS.md."""
    if confidence < 0.85 or not correction:
        return
    try:
        agents_file = ROOT_DIR / "AGENTS.md"
        if not agents_file.exists():
            return
        content = agents_file.read_text(encoding="utf-8")
        clean_rule = correction.strip()
        if len(clean_rule) > 150:
            clean_rule = clean_rule[:147] + "..."
        # Content-similarity dedup against THIS component's existing rules (the
        # same test the Qdrant store applies). The old exact-substring check let
        # every LLM RE-DISTILLATION of the same failure append another slightly
        # rephrased near-duplicate line to AGENTS.md.
        line_prefix = f"- **Rule ({component})**: "
        for line in content.splitlines():
            if line.startswith(line_prefix):
                if _corrections_similar(line[len(line_prefix) :], clean_rule):
                    return
        new_entry = f"{line_prefix}{clean_rule}\n"
        marker = "## Self-Healing & Self-Learning Fixes\n"
        if marker in content:
            content = content.replace(marker, marker + "\n" + new_entry, 1)
            agents_file.write_text(content, encoding="utf-8")
            logger.info(
                "Recorded high-confidence ASPO rule to AGENTS.md for component '%s'",
                component,
            )
    except Exception as e:
        logger.warning("Could not record ASPO rule to AGENTS.md: %s", e)


# Structured reflection template (per Reflexion best practices). Free-form rules
# are worthless; rules must reference the concrete failure and a do-not-repeat.
DISTILLER_PROMPT = """You are the ASPO Rule Distiller. Convert agent failures into reusable correction rules.

Task Context: {task_description}
Component: {component}
Agent Output/Action: {content_preview}
Failure Reason: {error_message}

Produce a structured reflection with EXACTLY this format:
<reflection>
<failure_summary>
[1-2 sentences: what the agent did and what failed]
</failure_summary>
<root_cause>
[1 sentence: the actual root cause]
</root_cause>
<next_attempt_rules>
[2-3 concrete, actionable rules referencing specific tools/constraints]
</next_attempt_rules>
<do_not_repeat>
[1 sentence: the exact mistake to never repeat]
</do_not_repeat>
</reflection>"""


# Shared-scope reflection rules (cross-agent lessons). Only confident, generic,
# non-agent-specific failures may be shared; everything else stays agent-siloed.
# Retrieval of shared rules is env-gated (SWARM_SHARED_REFLEXION=1, off by default).
SHARED_SCOPE_MIN_CONFIDENCE = 0.7
SHARED_SCOPE_GENERIC_MARKERS = (
    "file not found",
    "permission denied",
    "no such file",
    "unknown operation",
    "timeout",
    "timed out",
    "slot was busy",
    "malformed json",
    "malformed",
    "truncated",
    "parse",
)


def _auto_scope(confidence: float, failure_reason: str) -> str:
    """Conservative scope assignment for stored reflexion rules.

    Only rules that are (a) confident enough (>= SHARED_SCOPE_MIN_CONFIDENCE)
    and (b) whose failure reason matches an explicit allowlist of generic,
    cross-agent failure categories qualify for scope='shared'. Everything else
    stays 'agent' so agent-specific lessons never leak across agents.
    """
    if confidence < SHARED_SCOPE_MIN_CONFIDENCE:
        return "agent"
    reason = (failure_reason or "").lower()
    if not any(marker in reason for marker in SHARED_SCOPE_GENERIC_MARKERS):
        return "agent"
    return "shared"


class ReflectionService:
    def __init__(
        self,
        qdrant_url: str = "http://127.0.0.1:6333",
        collection_name: str = "ReflexionMemory",
    ):
        self.client = AsyncQdrantClient(url=qdrant_url)
        self.collection = collection_name
        self.embedder = EmbeddingService()
        # Cross-loop-safe init: do NOT eagerly bind an _init_collection task to
        # whatever loop is running at construction. The singleton is shared
        # across the server lifespan, CLI threads (asyncio.run), the watch-loop
        # daemon, and healing threads — an eager task created on loop A and
        # awaited from loop B raises CancelledError (proven: pending cross-loop
        # await). Init runs lazily on the CALLER's loop via _wait_init, guarded
        # by an asyncio.Lock so concurrent first-use callers do not double-create.
        self._init_task = None
        self._ensured = False
        self._init_lock = asyncio.Lock()

    async def _wait_init(self):
        if self._ensured:
            return
        async with self._init_lock:
            if self._ensured:
                return
            self._ensured = await self._init_collection()

    async def _init_collection(self) -> bool:
        try:
            collections_response = await self.client.get_collections()
            collections = collections_response.collections
            if not any(c.name == self.collection for c in collections):
                await self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=768, distance=Distance.COSINE),
                )
            return True
        except Exception as e:
            logger.error("Failed to init ReflexionMemory: %s", e)
            return False

    async def check_for_past_mistakes(
        self, task_context: str, threshold: float = 0.75, max_chars: int = 700
    ) -> str:
        """Ranked retrieval with recency+confidence decay (top-k, not single hit).
        score = similarity · decay(age) · confidence; returns the single best rule.

        When SWARM_SHARED_REFLEXION=1 (off by default), an extra top-k query
        filtered to scope='shared' is merged in, so generic lessons any agent
        produced are surfaced regardless of their component — de-duplicated
        against the agent's own results and ranked by the same decay formula.
        """
        await self._wait_init()
        try:
            embedding = await self.embedder.embed(task_context)
            # qdrant-client >=1.18: AsyncQdrantClient has no .search(); use query_points.
            response = await self.client.query_points(
                collection_name=self.collection,
                query=embedding,
                limit=5,
                score_threshold=threshold,
            )
            results = list(getattr(response, "points", response))
            if os.environ.get("SWARM_SHARED_REFLEXION") == "1":
                shared_response = await self.client.query_points(
                    collection_name=self.collection,
                    query=embedding,
                    limit=5,
                    score_threshold=threshold,
                    query_filter=Filter(
                        must=[
                            FieldCondition(
                                key="scope", match=MatchValue(value="shared")
                            )
                        ]
                    ),
                )
                shared_results = list(
                    getattr(shared_response, "points", shared_response)
                )
                seen_ids = set()
                merged = []
                for r in results + shared_results:
                    rid = getattr(r, "id", None)
                    if rid is not None and rid in seen_ids:
                        continue
                    if rid is not None:
                        seen_ids.add(rid)
                    merged.append(r)
                results = merged
            if results:
                # 2026 move 5a: when the reranker runs, its relevance judgment is
                # the PRIMARY rank — it judges the actual query against each
                # candidate, which dense similarity only approximates. Recency
                # decay + confidence are a MINIMUM FILTER and a TIEBREAK, never a
                # co-equal multiplier: multiplying them against the rerank score
                # would let a dense-nearest-but-wrong precedent win on its dense
                # score, which is exactly the failure mode this fix exists for.
                # On reranker outage, fall back to the dense ordering (existing
                # behavior) so retrieval still works, just less precisely.
                now = time.time()
                reranked_by_score = {}
                try:
                    candidates = [
                        {
                            "id": getattr(r, "id", None),
                            "payload": r.payload or {},
                            "_dense": r.score or 0.0,
                            "_age": now
                            - float((r.payload or {}).get("timestamp", now)),
                        }
                        for r in results
                    ]
                    from runtime_v2.services.memory_core import rerank_memories

                    rr = await asyncio.to_thread(
                        rerank_memories,
                        task_context,
                        [{"payload": c["payload"], "id": c["id"]} for c in candidates],
                    )
                    for item in rr:
                        cid = item.get("id")
                        if cid is not None:
                            reranked_by_score[cid] = float(item.get("score", 0.0))
                except Exception as _rerank_err:
                    logger.debug(
                        "Rerank of past-mistake candidates failed; using dense scores: %s",
                        _rerank_err,
                    )

                best = None
                best_rank = -1.0
                for r in results:
                    payload = r.payload or {}
                    cid = getattr(r, "id", None)
                    sim = r.score or 0.0
                    age = now - float(payload.get("timestamp", now))
                    # Exponential half-life (60 days) with a 0.40 floor so proven rules remain active
                    decay = max(0.40, 0.5 ** (age / (60.0 * 86400.0)))
                    confidence = float(payload.get("confidence", 0.5))
                    # Minimum-confidence filter: never surface a rule whose
                    # confidence has decayed below the retrieval threshold.
                    if confidence * decay < 0.15:
                        continue
                    if cid is not None and cid in reranked_by_score:
                        # Rerank is PRIMARY; decay is the tiebreak.
                        rank = reranked_by_score[cid]
                        rank += decay * 0.001  # tiny tiebreak for equal rerank scores
                    else:
                        rank = sim * decay * confidence  # dense fallback ordering
                    if rank > best_rank:
                        best_rank = rank
                        best = payload
                if best:
                    correction = best.get("correction", "")
                    do_not = best.get("do_not_repeat", "")
                    hint = f"WARNING: A similar approach previously failed. Advice: {correction}"
                    if do_not:
                        hint += f" Do NOT repeat: {do_not}"
                    # Cap lesson length so injected warnings never eat the context
                    # budget (2026: keep reflection lessons at ~10-20% of context).
                    return hint[:max_chars]
        except Exception as e:
            logger.warning("Failed to check ReflexionMemory: %s", e)
        return ""

    async def store_reflexion(
        self,
        task: str,
        action: str,
        failure_reason: str,
        correction: str,
        component: str = "unknown",
        confidence: float = 0.7,
        do_not_repeat: str = "",
        scope: str | None = None,
    ):
        """Persist a reflexion rule. scope defaults to 'agent' and is auto-assigned
        via _auto_scope() when not given (confident + generic failure => 'shared')."""
        await self._wait_init()
        try:
            if scope is None:
                scope = _auto_scope(confidence, failure_reason)
            embedding = await self.embedder.embed(task)
            # Deterministic point id keyed on (component, failure_reason):
            # a repeated failure overwrites the prior rule instead of flooding
            # the collection with near-duplicate points (observed: 60 copies of
            # "File not found: x.py" crowded out specific per-task lessons).
            point_id = str(
                uuid.uuid5(uuid.NAMESPACE_DNS, f"{component}|{failure_reason}")
            )
            # 2026 L5 (trust-gated consolidation): a repeated failure must
            # REINFORCE the stored rule, not flat-overwrite it. Read the existing
            # point; if present, bump its count and raise confidence (capped) so a
            # recurring failure's lesson accumulates evidence and becomes more
            # load-bearing, while a one-off stays tentative. The strongest
            # correction is retained (longer/more-specific wins).
            existing = None
            retrieve_failed = False
            async with _get_point_lock(point_id):
                try:
                    resp = await self.client.retrieve(
                        collection_name=self.collection,
                        ids=[point_id],
                        with_payload=True,
                        with_vectors=False,
                    )
                    if resp:
                        existing = resp[0].payload or {}
                except Exception as exc:
                    # 2026 L5 (trust-gated consolidation): a GENUINE retrieve failure
                    # (DB/vector-store timeout, corrupt payload) is NOT the same as "no
                    # prior record exists". The old behavior silently degraded both to
                    # existing=None — a flaky retrieve would misclassify a real
                    # CONFLICT as a brand-new first-write (silently discarding history,
                    # exactly what L5 forbids) and stop confidence from accumulating for
                    # reasons unrelated to the fact. Now it's logged loudly and the
                    # write is FLAGGED so the failure is observable, not folded into the
                    # happy-path default.
                    retrieve_failed = True
                    logger.warning(
                        "Reflexion retrieve FAILED for (component=%s, reason=%s): %s — "
                        "treating as unclassified first-write (history not consulted)",
                        component,
                        failure_reason,
                        exc,
                    )
                    existing = None

                # 2026 L5 (trust-gated consolidation): judge a write as SAME-FACT
                # (reinforce: bump count + confidence) vs CONFLICT (overwrite + log)
                # based on the CORRECTION CONTENT, not on the key having been written
                # before. Same precedence discipline as the L2 classifier: a repeated
                # write with DIFFERENT advice is a conflict (never always-reinforce),
                # an identical/rephrased repeat is same-fact (never always-overwrite).
                verdict = _classify_rule(existing, correction)
                count = int(existing.get("count", 1) or 1) if existing else 1
                prev_conf = (
                    float(existing.get("confidence", confidence) or confidence)
                    if existing
                    else confidence
                )
                if verdict == _RULE_CONFLICT:
                    # A genuinely different correction for the same failure_reason: the
                    # old content is SUPERSEDED (overwrite with the new) and the
                    # conflict is logged, not silently discarded. Count carries over as
                    # recurrence evidence; confidence for the new content is modest.
                    if existing and existing.get("correction") not in (
                        None,
                        "",
                        correction,
                    ):
                        logger.info(
                            "Reflexion CONFLICT for (component=%s, reason=%s): overwriting "
                            "prior rule %r with new rule %r",
                            component,
                            failure_reason,
                            str(existing.get("correction"))[:60],
                            str(correction)[:60],
                        )
                    best_conf = max(confidence, prev_conf * 0.5)
                    scope = existing.get("scope", scope) if existing else scope
                else:
                    # SAME FACT: reinforce — a true repeat (existing point) increments
                    # count and raises confidence (capped) so a recurring failure
                    # accumulates evidence and becomes load-bearing, while a first
                    # write stays at count=1 / tentative. Keep the more specific
                    # (longer) correction.
                    if existing:
                        count = count + 1
                        best_conf = min(0.98, prev_conf + 0.08)
                    else:
                        count = 1
                        best_conf = prev_conf
                    if not correction and existing and existing.get("correction"):
                        correction = existing.get("correction")
                    if not do_not_repeat and existing and existing.get("do_not_repeat"):
                        do_not_repeat = existing.get("do_not_repeat")
                    best_conf = max(best_conf, confidence)
                await self.client.upsert(
                    collection_name=self.collection,
                    points=[
                        PointStruct(
                            id=point_id,
                            vector=embedding,
                            payload={
                                "task": task,
                                "action": action,
                                "failure_reason": failure_reason,
                                "correction": correction,
                                "do_not_repeat": do_not_repeat,
                                "component": component,
                                "scope": scope,
                                "timestamp": time.time(),
                                "confidence": best_conf,
                                "count": count,
                                "retrieve_failed": retrieve_failed,
                            },
                        )
                    ],
                    wait=True,
                )
                await asyncio.to_thread(
                    _record_rule_to_agents_md, component, correction, best_conf
                )
        except Exception as e:
            logger.error("Failed to store reflexion: %s", e)


_service = None


def get_reflection_service() -> ReflectionService:
    global _service
    if _service is None:
        _service = ReflectionService()
    return _service


def get_latest_failure(filepath: Path) -> dict | None:
    chunk_size = 4096
    with open(filepath, "rb") as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        position = file_size
        buffer = b""

        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            f.seek(position)
            chunk = f.read(read_size)
            buffer = chunk + buffer

            lines = buffer.split(b"\n")
            if position > 0:
                buffer = lines.pop(0)

            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line.decode("utf-8"))
                    err = entry.get("error")
                    if err is not None and err != "" and str(err).lower() != "null":
                        # Prefer REAL agent failures (entries carrying a component or
                        # agent id) over genetic-kernel evaluation noise. The diary is
                        # dominated by kernel eval entries (event=action/evaluation with
                        # org/generation/avg_fitness) whose errors are sandbox/fitness
                        # artifacts ("http_422", "[WinError 10061]", ...) — distilling
                        # those produced the 137 component:"unknown" noise rules that
                        # swamped ReflexionMemory. Real agent tool failures carry a
                        # component (agent_id) — those are what the distiller should learn from.
                        is_agent_failure = bool(
                            entry.get("component") or entry.get("agent")
                        )
                        if is_agent_failure:
                            return entry
                except Exception as exc:
                    logger.debug("Failed to parse diary line: %s", exc)
                    pass
    # No agent-tagged failure found — fall back to the raw last error so the
    # distiller still has SOMETHING rather than silently skipping.
    with open(filepath, "rb") as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        position = file_size
        buffer = b""
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            f.seek(position)
            chunk = f.read(read_size)
            buffer = chunk + buffer
            lines = buffer.split(b"\n")
            if position > 0:
                buffer = lines.pop(0)
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line.decode("utf-8"))
                    err = entry.get("error")
                    if err is not None and err != "" and str(err).lower() != "null":
                        return entry
                except Exception as exc:
                    logger.debug("Failed to parse fallback diary line: %s", exc)
                    pass
    return None


async def _distill(distiller_content: str, fix_class: str | None = None) -> str:
    """Run the distiller LLM call. Cloud DeepSeek V4 flash first (fast, no
    thinking tokens), local qwen3.5-4b fallback (needs /no_think system lead +
    roomy token budget so reasoning + content both fit at ~5 tok/s).

    fix_class (from diagnostician.py): "prompt_sensitivity" means the failure
    is fixable via rule/prompt changes — distillation is worthwhile.
    "model_variability" means the model itself can't do it — skip the LLM call
    entirely, it won't produce an actionable rule. Missing/unknown defaults to
    running distillation (fail-open)."""

    if fix_class == "model_variability":
        logger.info(
            "Skipping LLM distillation for model_variability failure (not diagnosable)"
        )
        return ""

    from runtime_v2.services.fallback_manager import (
        is_model_cooled_down,
        record_model_failure,
    )

    attempts = []
    # 1. Verified free / healthy providers first (NVIDIA NIM DeepSeek-v4-Flash-0731)
    if os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_NIM_API_KEY"):
        os.environ.setdefault(
            "NVIDIA_NIM_API_KEY", os.environ.get("NVIDIA_API_KEY", "")
        )
        attempts.append(
            {
                "model": "nvidia_nim/deepseek-ai/deepseek-v4-flash-0731",
                "messages": [{"role": "user", "content": distiller_content}],
                "max_tokens": DISTILLER_MAX_TOKENS_CLOUD,
                "timeout": 180.0,
            }
        )
    # 2. Gemini fallback
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        attempts.append(
            {
                "model": "gemini/gemini-2.0-flash",
                "messages": [{"role": "user", "content": distiller_content}],
                "max_tokens": DISTILLER_MAX_TOKENS_CLOUD,
                "timeout": 90.0,
            }
        )
    # 3. Groq fallback
    if os.environ.get("GROQ_API_KEY"):
        attempts.append(
            {
                "model": "groq/llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": distiller_content}],
                "max_tokens": DISTILLER_MAX_TOKENS_CLOUD,
                "timeout": 90.0,
            }
        )
    # 4. OpenRouter fallback
    if os.environ.get("OPENROUTER_API_KEY"):
        attempts.append(
            {
                "model": "openrouter/stealth/ox-alpha",
                "messages": [{"role": "user", "content": distiller_content}],
                "max_tokens": 2000,
                "timeout": 120.0,
            }
        )
    # 5. OpenCode / DeepSeek paid fallbacks
    if os.environ.get("OPENAI_API_KEY"):
        api_base = os.getenv("OPENAI_API_BASE", "https://api.opencode.go/v1")
        api_key = os.environ["OPENAI_API_KEY"]
        attempts.append(
            {
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": distiller_content}],
                "api_base": api_base,
                "api_key": api_key,
                "custom_llm_provider": "openai",
                "max_tokens": DISTILLER_MAX_TOKENS_CLOUD,
                "timeout": 90.0,
            }
        )
    if os.environ.get("DEEPSEEK_API_KEY"):
        attempts.append(
            {
                "model": "deepseek/deepseek-chat",
                "messages": [{"role": "user", "content": distiller_content}],
                "max_tokens": DISTILLER_MAX_TOKENS_CLOUD,
                "timeout": 90.0,
            }
        )
    # 6. Fast local summarizer
    attempts.append(
        {
            "model": "qwen3.5-0.8b",
            "messages": [
                {"role": "system", "content": "/no_think\n\n"},
                {"role": "user", "content": distiller_content},
            ],
            "api_base": "http://127.0.0.1:8084/v1",
            "api_key": "llama",
            "custom_llm_provider": "openai",
            "max_tokens": DISTILLER_MAX_TOKENS_LOCAL,
            "timeout": 120.0,
        }
    )

    last_exc = None
    for cfg in attempts:
        model_name = cfg.get("model", "")
        if is_model_cooled_down(model_name):
            logger.info("Distiller skipping cooled down model: %s", model_name)
            continue
        try:
            async with asyncio.timeout(cfg["timeout"]):
                res = await acompletion(**cfg)
            content = res.choices[0].message.content or ""
            if content.strip():
                try:
                    from runtime_v2.services.usage_log import record_response

                    record_response(res, cfg.get("model", ""), source="distill")
                except Exception as usage_err:  # noqa: BLE001
                    logger.debug("usage log skipped: %s", usage_err)
                return content
            logger.warning("Distiller returned empty content from %s", cfg["model"])
        except Exception as e:
            last_exc = e
            msg = str(e)
            record_model_failure(model_name, msg)
            logger.warning("Distiller via %s failed: %s", cfg["model"], e)
            # Retry cloud models with fewer tokens on 402 credit exhaustion
            if "can only afford" in msg and cfg.get("max_tokens", 0) > 128:
                import re

                match = re.search(r"can only afford (\d+)", msg)
                if match:
                    affordable = int(match.group(1))
                    clamped = max(128, min(affordable - 64, 512))
                    logger.warning(
                        "Distiller retrying %s with max_tokens=%d",
                        cfg["model"],
                        clamped,
                    )
                    cfg_copy = dict(cfg)
                    cfg_copy["max_tokens"] = clamped
                    try:
                        async with asyncio.timeout(cfg_copy["timeout"]):
                            res = await acompletion(**cfg_copy)
                        content = res.choices[0].message.content or ""
                        if content.strip():
                            try:
                                from runtime_v2.services.usage_log import (
                                    record_response,
                                )

                                record_response(
                                    res, cfg.get("model", ""), source="distill_retry"
                                )
                            except Exception as usage_err:  # noqa: BLE001
                                logger.debug("usage log skipped: %s", usage_err)
                            return content
                    except Exception as exc:
                        # BUG FIX: was silently swallowing the 402 token-reduction
                        # retry failure with no log. Now debug-logged.
                        logger.debug(
                            "402 retry with fewer tokens failed for %s: %s",
                            cfg["model"],
                            exc,
                        )
            # 402 = OpenRouter credit exhaustion — fall through to local qwen3.5-4b.
            # With the MTP 4B at ~15 t/s (vs old 5 t/s) the local fallback is viable.
    if last_exc:
        raise last_exc
    return ""


async def run_reflection():
    try:
        latest_failure = await asyncio.to_thread(get_latest_failure, DIARY_PATH)
    except FileNotFoundError:
        logger.info("No organism diary found. Skipping reflection.")
        return

    if not latest_failure:
        logger.info("No failures detected in diary.")
        return

    task_desc = latest_failure.get("task", "Unknown Task")
    content = latest_failure.get("content_preview", "")
    error_msg = latest_failure.get("error")
    component = str(
        latest_failure.get("component") or latest_failure.get("agent") or "unknown"
    )
    fix_class = latest_failure.get("fix_class")
    if fix_class is None:
        try:
            from swarm_os.healing.diagnostician import Diagnostician

            hypotheses = Diagnostician().diagnose(
                {"detail": error_msg or "", "component": component}
            )
            if hypotheses:
                fix_class = hypotheses[0].get("fix_class")
        except Exception as exc:
            # BUG FIX: was silently swallowing diagnostician failures; now debug-logged
            # so we can tell when the Diagnostician import/diagnose path breaks.
            logger.debug("Diagnostician failed during reflection: %s", exc)

    logger.info(f"Distilling failure: {error_msg}")

    distiller_content = DISTILLER_PROMPT.format(
        task_description=task_desc,
        component=component,
        content_preview=content,
        error_message=error_msg,
    )

    try:
        rule_full = await _distill(distiller_content, fix_class=fix_class)

        # Parse structured reflection; fall back to extracting any rule text.
        def _field(tag: str) -> str:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", rule_full, re.DOTALL)
            return m.group(1).strip() if m else ""

        failure_summary = _field("failure_summary")
        root_cause = _field("root_cause")
        next_rules = _field("next_attempt_rules")
        do_not = _field("do_not_repeat")

        # Compose the stored correction from the structured fields.
        parts = []
        if failure_summary:
            parts.append(f"Failure: {failure_summary}")
        if root_cause:
            parts.append(f"Root cause: {root_cause}")
        if next_rules:
            parts.append(f"Next-attempt rules: {next_rules}")
        if do_not:
            parts.append(f"Do NOT repeat: {do_not}")
        correction = " | ".join(parts) if parts else None

        if not correction:
            legacy = re.search(r"<new_rule>(.*?)</new_rule>", rule_full, re.DOTALL)
            correction = (
                legacy.group(1).strip() if legacy else (rule_full or "").strip()[:500]
            )
        if not correction:
            logger.warning("No rule generated by distiller.")
            return

        logger.info(f"Distilled new rule: {correction}")

        svc = get_reflection_service()
        await svc.store_reflexion(
            task_desc, content, error_msg, correction, component=component
        )
        logger.info("Successfully saved reflexion rule to Qdrant ReflexionMemory.")

    except Exception as e:
        logger.error(f"Distiller phase failed: {e}")


if __name__ == "__main__":
    asyncio.run(run_reflection())
