"""Lesson Manager — governance layer between ReflexionMemory and the agent prompt.

Enforces:
  - 300-token budget for active lessons
  - Deduplication (similar rules merge)
  - Conflict detection (contradictory rules blocked)
  - Atomic promotion (only verified rules enter the active set)
  - Recency + effectiveness weighting
  - Rollback (disable one lesson, prompt reverts)
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    PointIdsList,
    PointStruct,
)

from swarm_os.services.embedding_service import EmbeddingService

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTIVE_COLLECTION = "ActiveLessons"
EVAL_COLLECTION = "EvalLessons"
MAX_ACTIVE_TOKENS = 300  # hard ceiling for the WHOLE active lesson set (governance invariant)
MAX_RULES = 8
MAX_RULE_TOKENS = 50  # per-rule ceiling (governance invariant)
MIN_CONFIDENCE = 0.4
CONFLICT_SIMILARITY_THRESHOLD = 0.85  # above this → likely conflict
DEDUPE_SIMILARITY_THRESHOLD = 0.75  # above this → merge candidates


# --- Per-request evaluation context (isolation) -----------------------------
# The candidate lesson is delivered to a SPECIFIC evaluation request only —
# never written to the global ACTIVE collection. The snapshot is immutable
# once registered, so a candidate that mutates mid-evaluation cannot change
# what the worker saw.
@dataclass
class EvaluationContext:
    evaluation_id: str
    candidate_id: str
    task_id: str
    lesson: str
    expires_at: float


_EVAL_CONTEXTS: dict[str, EvaluationContext] = {}
_EVAL_LOCK = threading.Lock()
EVAL_CONTEXT_TTL_S = 1800


def register_eval_context(
    evaluation_id: str,
    candidate_id: str,
    task_id: str,
    lesson: str,
    ttl_s: int = EVAL_CONTEXT_TTL_S,
) -> EvaluationContext:
    """Register an immutable evaluation snapshot. Scoped to one request id."""
    ctx = EvaluationContext(
        str(evaluation_id), str(candidate_id), str(task_id), str(lesson), time.time() + ttl_s
    )
    with _EVAL_LOCK:
        _EVAL_CONTEXTS[str(evaluation_id)] = ctx
    return ctx


def get_eval_context(evaluation_id: str | None) -> EvaluationContext | None:
    """Return a live (non-expired) evaluation context, or None. Fail-closed."""
    if not evaluation_id:
        return None
    with _EVAL_LOCK:
        ctx = _EVAL_CONTEXTS.get(str(evaluation_id))
    if not ctx or ctx.expires_at < time.time():
        return None
    return ctx


def clear_eval_context(evaluation_id: str | None) -> None:
    if not evaluation_id:
        return
    with _EVAL_LOCK:
        _EVAL_CONTEXTS.pop(str(evaluation_id), None)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CandidateRule:
    trigger: str  # "when task requires local code repair"
    action: str  # "use filesystem to read the test file first"
    component: str  # "coder"
    source_run_id: str
    source_hypothesis: str
    tokens: int = 0
    tool_count: int = 0
    created_at: str = ""
    status: str = "candidate"  # candidate | evaluating | promoted | rejected
    id: str = ""
    evaluation_result: dict | None = None

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not self.tokens:
            self.tokens = _estimate_tokens(self.rule_text)

    @property
    def rule_text(self) -> str:
        return f"{self.trigger}: {self.action}"

    def to_lesson_text(self) -> str:
        return f"{self.action}"


@dataclass
class ActiveLesson:
    rule: str
    confidence: float
    effectiveness: float
    source_candidates: list[str] = field(default_factory=list)
    version: int = 1
    created_at: str = ""
    last_verified_at: str = ""
    superseded_by: str | None = None
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not self.last_verified_at:
            self.last_verified_at = self.created_at

    @property
    def tokens(self) -> int:
        return _estimate_tokens(self.rule)


@dataclass
class Conflict:
    lesson_a_id: str
    lesson_b_id: str
    reason: str
    similarity: float


@dataclass
class PruningReport:
    removed: list[str]
    reason: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN_ENCODING = None


def estimate_tokens(text: str) -> int:
    """ONE authoritative token estimator for the lesson governance layer.

    Uses tiktoken (cl100k) when available; falls back to a conservative
    ``max(1, len(text) // 3)`` estimate. Used by EVERY hard budget check so
    rule-level and set-level reasoning cannot disagree.
    """
    global _TOKEN_ENCODING
    try:
        if _TOKEN_ENCODING is None:
            import tiktoken

            _TOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
        return len(_TOKEN_ENCODING.encode(text))
    except Exception:
        return max(1, len(text) // 3)


def _estimate_tokens(text: str) -> int:
    """Back-compat alias → the singl e authoritative estimator."""
    return estimate_tokens(text)


def _jaccard_tokens(a: str, b: str) -> float:
    """Jaccard similarity over whitespace-split tokens."""
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _lessons_fully_within(
    block: str, lessons: list[ActiveLesson]
) -> list[ActiveLesson]:
    """Return the lessons whose rendered line survives INTACT in ``block``.

    ``block`` may have been truncated at ``max_chars``, cutting a rule
    mid-sentence.  A rule that only partially reached the model was not
    fully delivered, so its lesson must not be attested as delivered.
    Numbered lines are ``"N. rule"``; a lesson counts only if its own line is
    present whole.
    """
    out: list[ActiveLesson] = []
    for lesson in lessons:
        rule = (lesson.rule or "").strip()
        if not rule:
            continue
        if any(line.endswith(rule) for line in block.splitlines()):
            out.append(lesson)
    return out


def _is_contradiction(a: str, b: str) -> bool:
    """Detect obvious negation patterns between two rules."""
    negations = {"never", "do not", "don't", "no ", "not "}
    a_lower = a.lower()
    b_lower = b.lower()
    for neg in negations:
        if neg in a_lower and neg in b_lower:
            # Both negated — not a contradiction, just redundancy
            continue
        if neg in a_lower and neg not in b_lower:
            # One negated, one not — check if they share subject
            a_subject = a_lower.replace(neg, "").strip()[:30]
            if a_subject and a_subject in b_lower:
                return True
        if neg not in a_lower and neg in b_lower:
            b_subject = b_lower.replace(neg, "").strip()[:30]
            if b_subject and b_subject in a_lower:
                return True
    return False


# ---------------------------------------------------------------------------
# Lesson Manager
# ---------------------------------------------------------------------------

class LessonManager:
    """Governance layer: dedupe, conflict detection, budget enforcement,
    versioned storage, and selective retrieval for active lessons."""

    def __init__(self, client: AsyncQdrantClient | None = None):
        self._client = client
        self._embedding_client = None  # lazy init

    async def _get_client(self) -> AsyncQdrantClient:
        if self._client is None:
            import os
            qdrant_url = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
            self._client = AsyncQdrantClient(url=qdrant_url)
        return self._client

    async def _embed(self, text: str) -> list[float]:
        if self._embedding_client is None:
            self._embedding_client = EmbeddingService()
        return await self._embedding_client.embed(text)

    # -- Storage --

    async def store(self, lesson: ActiveLesson, collection_name: str = ACTIVE_COLLECTION) -> str:
        """Persist an active lesson. Returns the lesson ID.

        Enforces the two hard governance invariants at write time:
          - a single rule may never exceed MAX_RULE_TOKENS
          - the whole active set may never exceed MAX_ACTIVE_TOKENS
        Raising here (fail-closed) is the promotion gate's backstop; the
        Promotion Gate in prompt_repairer pre-checks the same limits before
        it ever calls store(), so a raise means a governance bug, not a
        routine rejection.
        """
        rule_tokens = estimate_tokens(lesson.rule)
        if rule_tokens > MAX_RULE_TOKENS:
            raise ValueError(
                f"rule exceeds MAX_RULE_TOKENS={MAX_RULE_TOKENS} "
                f"(rule={rule_tokens} tokens)"
            )
            
        if collection_name == ACTIVE_COLLECTION:
            active_lessons = await self.get_all()
            # If this is an update, we shouldn't count the old version's tokens
            current_tokens = sum(estimate_tokens(l.rule) for l in active_lessons if l.id != lesson.id)
            if current_tokens + rule_tokens > MAX_ACTIVE_TOKENS:
                raise ValueError(
                    f"cannot store lesson: would exceed MAX_ACTIVE_TOKENS={MAX_ACTIVE_TOKENS} "
                    f"(current={current_tokens}, new={rule_tokens})"
                )
                
        client = await self._get_client()
        vector = await self._embed(lesson.rule)
        payload = {
            "rule": lesson.rule,
            "confidence": lesson.confidence,
            "effectiveness": lesson.effectiveness,
            "source_candidates": json.dumps(lesson.source_candidates),
            "version": lesson.version,
            "created_at": lesson.created_at,
            "last_verified_at": lesson.last_verified_at,
            "superseded_by": lesson.superseded_by or "",
        }
        await client.upsert(
            collection_name=collection_name,
            points=[PointStruct(id=lesson.id, vector=vector, payload=payload)],
        )
        return lesson.id

    async def remove(self, lesson_id: str, collection_name: str = ACTIVE_COLLECTION) -> bool:
        """Remove a lesson by ID. Returns True if found and removed."""
        client = await self._get_client()
        result = await client.delete(
            collection_name=collection_name,
            points_selector=PointIdsList(points=[lesson_id]),
        )
        return result.deleted_count > 0

    # -- Retrieval --

    async def retrieve(
        self, task_context: str, max_tokens: int = MAX_ACTIVE_TOKENS
    ) -> list[str]:
        """Retrieve the most relevant active lessons within the token budget.

        Returns a list of rule strings, sorted by relevance.
        Never returns more than fits in max_tokens.
        """
        client = await self._get_client()
        vector = await self._embed(task_context)

        results = await client.query_points(
            collection_name=ACTIVE_COLLECTION,
            query=vector,
            limit=MAX_RULES,
            score_threshold=MIN_CONFIDENCE,
        )

        if not results.points:
            return []

        # Sort by score * effectiveness (combined relevance)
        scored = []
        for hit in results.points:
            payload = hit.payload or {}
            eff = payload.get("effectiveness", 0.5)
            combined = hit.score * max(eff, 0.1)
            scored.append((combined, payload.get("rule", "")))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Pack into budget
        selected = []
        budget_remaining = max_tokens
        for _, rule in scored:
            rule_tokens = _estimate_tokens(rule)
            if rule_tokens <= budget_remaining:
                selected.append(rule)
                budget_remaining -= rule_tokens
            else:
                break

        return selected

    async def _rank_active(
        self, task_context: str
    ) -> list[tuple[float, ActiveLesson]]:
        """Rank the non-superseded active set for this task.

        Ordering is by ``relevance x effectiveness``, then by the governed
        ``(effectiveness, version)`` key as a deterministic tie-break so two
        lessons with equal score always render in the same order.

        Two properties are deliberate and load-bearing:

        * **Relevance is advisory, not a filter.** A lesson with no similarity
          signal still participates. Retrieval decides ORDER among lessons that
          fit the budget, never which lessons are eligible. Eligibility is a
          governance question (supersession, safety, budget) and a relevance
          threshold would make it a retrieval question — a threshold on a store
          that contains contradicted lessons cannot separate stale from current
          (AUROC 0.59, arXiv:2606.26511).
        * **Relevance is optional.** If the query cannot be embedded, ranking
          degrades to the governed order instead of failing closed, because a
          transient embedder outage must not silently withdraw every lesson
          from the prompt.

        Returns ``(score, lesson)`` with score in ``[0, 1]``; ``0.0`` when
        relevance is unavailable.
        """
        try:
            lessons = await self.get_all()
        except Exception as exc:  # noqa: BLE001
            _log.warning("lesson ranking: retrieval failed: %s", exc)
            return []

        active = [l for l in lessons if not l.superseded_by]
        scores: dict[str, float] = {}
        query = (task_context or "").strip()
        if query and active:
            try:
                vector = await self._embed(query)
                results = await self._client_query_active(vector)
                for hit in results:
                    lid = hit.id if isinstance(hit.id, str) else str(hit.id)
                    scores[lid] = float(hit.score or 0.0)
            except Exception as exc:  # noqa: BLE001 - degrade, never withdraw
                _log.warning(
                    "lesson ranking: relevance unavailable, using governed order: %s",
                    exc,
                )

        ranked: list[tuple[float, ActiveLesson]] = []
        for lesson in active:
            rel = scores.get(lesson.id, 0.0)
            combined = rel * max(0.0, float(lesson.effectiveness or 0.0))
            ranked.append((combined, lesson))
        ranked.sort(
            key=lambda item: (
                item[0],
                max(0.0, float(item[1].effectiveness or 0.0)),
                item[1].version,
                item[1].id,
            ),
            reverse=True,
        )
        return ranked

    async def _client_query_active(self, vector: list[float]):
        """Dense query against the ACTIVE collection. Isolated for testability."""
        client = await self._get_client()
        return await client.query_points(
            collection_name=ACTIVE_COLLECTION,
            query=vector,
            limit=MAX_RULES * 2,
            score_threshold=MIN_CONFIDENCE,
        )

    async def _select_for_render(
        self,
        task_context: str = "",
        eval_id: str | None = None,
        exclude_ids: Iterable[str] | None = None,
    ) -> list[tuple[ActiveLesson | None, str]]:
        """Select the ordered (lesson, rule) pairs that will actually render.

        THE single source of truth for governed lesson selection. ``render_active_
        lessons()`` renders exactly these pairs, and any consumer that needs the
        *identity* of what was delivered (e.g. the F2 arm manifest) must derive
        it from here — never from an independent second selection.

        ``exclude_ids`` removes specific lessons from ELIGIBILITY. It exists so a
        caller can withhold a named lesson without reranking what remains —
        withholding must not promote a lesson that would otherwise have been
        budget-excluded, or the delivered set would shift for a reason unrelated
        to the exclusion. Excluded ids are therefore filtered BEFORE packing,
        and never re-added afterwards.

        Returns pairs of (ActiveLesson | None, rule_text). ``None`` marks the
        per-request evaluation snapshot, which is request-scoped and has no
        ACTIVE-collection identity.
        """
        excluded = {str(x).strip() for x in (exclude_ids or ()) if str(x).strip()}

        ranked = await self._rank_active(task_context)
        active = [
            lesson
            for _, lesson in ranked
            if not excluded or lesson.id not in excluded
        ]
        selected: list[tuple[ActiveLesson | None, str]] = []
        budget_tokens = MAX_ACTIVE_TOKENS
        # Per-request evaluation snapshot: delivered ONLY to the request that
        # carries the evaluation_id. The candidate is NEVER written to the
        # global ACTIVE collection, so unrelated workers never see it.
        ctx = get_eval_context(eval_id)
        if ctx:
            ev_lesson = (ctx.lesson or "").strip()
            # An explicit exclusion must also withhold the request-scoped
            # snapshot when it names this candidate. The snapshot has no ACTIVE
            # id of its own, so it is matched on the candidate it came from.
            if ctx.candidate_id and ctx.candidate_id in excluded:
                _log.info(
                    "render_active_lessons: EXCLUDED eval snapshot %s by id",
                    ctx.evaluation_id,
                )
                ev_lesson = ""
            ev_tokens = estimate_tokens(ev_lesson)
            try:
                from swarm_os.services.prompt_repairer import is_safe_lesson

                ev_safe = bool(ev_lesson) and is_safe_lesson(ev_lesson)
            except Exception:  # noqa: BLE001
                ev_safe = False
            if ev_safe and ev_tokens <= budget_tokens:
                selected.append((None, ev_lesson))
                budget_tokens -= ev_tokens
                _log.info(
                    "render_active_lessons: INCLUDED eval snapshot %s (candidate %s)",
                    ctx.evaluation_id,
                    ctx.candidate_id,
                )
        for lesson in active:
            rule = (lesson.rule or "").strip()
            if not rule:
                continue  # required-metadata validation: an empty rule never renders
            # Render-time defense in depth — never blindly trust the ACTIVE
            # collection. A malformed or unsafe object (e.g. a direct Qdrant
            # write) must not reach Robs merely because it is stored as ACTIVE.
            try:
                from swarm_os.services.prompt_repairer import is_safe_lesson

                if not is_safe_lesson(rule):
                    _log.warning(
                        "render_active_lessons: skipped unsafe stored lesson %s",
                        lesson.id,
                    )
                    continue
            except Exception:  # noqa: BLE001 - validation must never break rendering
                pass
            rule_tokens = estimate_tokens(rule)
            if rule_tokens > budget_tokens:
                continue  # cannot fit → next (already sorted by value)
            if rule_tokens > MAX_RULE_TOKENS:
                continue  # governance violation — never render an over-budget rule
            selected.append((lesson, rule))
            budget_tokens -= rule_tokens
        return selected

    async def render_active_lessons(
        self,
        task_context: str = "",
        max_chars: int = 700,
        eval_id: str | None = None,
        exclude_ids: Iterable[str] | None = None,
    ) -> str:
        """THE governed seam — render the deterministic [BEHAVIORAL LESSONS]
        block for a Robs worker prompt.

        This is the ONLY production path by which behavioral instruction may
        enter the worker's system prompt. It reads the active lesson set
        (versioned, deduped, budgeted, contradiction-checked by the Promotion
        Gate) and renders the *rule text only* — never raw Qdrant history,
        never the source trajectory, never confidence/evidence prose.

        Selection lives in ``_select_for_render`` — the single source of truth
        shared with any consumer that must attest to *which* lessons were
        delivered (see ``render_active_lessons_with_records``).  Do not
        re-implement selection here or elsewhere.

        ``task_context`` now participates: lessons are ordered by
        ``relevance x effectiveness`` for this request instead of by a static
        effectiveness value written once at promotion.  Relevance decides ORDER
        among lessons that already fit the budget; it never decides eligibility.
        Ranking degrades to governed order if the query cannot be embedded.

        ``exclude_ids`` withholds named lessons from eligibility without
        reranking the rest.

        Fail-closed: any retrieval error returns "" (no behavioral injection at
        all), and the rendered block is hard-bounded by both MAX_ACTIVE_TOKENS
        and ``max_chars`` even on partial retrieval.
        """
        block, _ = await self.render_active_lessons_with_records(
            task_context,
            max_chars=max_chars,
            eval_id=eval_id,
            exclude_ids=exclude_ids,
        )
        return block

    async def render_active_lessons_with_records(
        self,
        task_context: str = "",
        max_chars: int = 700,
        eval_id: str | None = None,
        exclude_ids: Iterable[str] | None = None,
    ) -> tuple[str, list[ActiveLesson]]:
        """Render the governed block AND return the ACTIVE lessons actually
        delivered, in render order.

        The returned lessons are precisely those whose rule text appears in the
        returned block.  A consumer that records "these lessons were delivered"
        MUST use this list rather than an independent selection, otherwise the
        recorded set can diverge from what was actually rendered (the F2 arm
        manifest hazard).  The request-scoped eval snapshot has no ACTIVE
        identity and is therefore excluded from the record list.
        """
        selected = await self._select_for_render(
            task_context, eval_id=eval_id, exclude_ids=exclude_ids
        )
        if not selected:
            return "", []
        lines = [rule for _, rule in selected]
        delivered = [lesson for lesson, _ in selected if lesson is not None]
        block = "\n".join(f"{i}. {r}" for i, r in enumerate(lines, 1))
        truncated = len(block) > max_chars
        if truncated:
            block = block[: max_chars]
            # A truncated block may cut a rule mid-sentence. Any lesson whose
            # rendered line does not appear COMPLETELY in the delivered block
            # was not fully delivered, so it is not a delivered record.
            delivered = _lessons_fully_within(block, delivered)
        return f"\n\n[BEHAVIORAL LESSONS]\n{block}", delivered

    async def get_all(self) -> list[ActiveLesson]:
        """Return all active lessons (for pruning/conflict checks).

        NOTE: this is a raw store read (dummy-vector dump).  It is the correct
        choice for governance passes that must consider the WHOLE set, and the
        wrong choice for anything that must agree with what the governed seam
        delivered — use ``_select_for_render`` for that.
        """
        client = await self._get_client()
        results = await client.query_points(
            collection_name=ACTIVE_COLLECTION,
            query=[0.0] * 768,  # dummy vector — we want all
            limit=MAX_RULES * 2,  # allow some headroom
        )
        lessons = []
        for hit in results.points:
            p = hit.payload or {}
            lessons.append(
                ActiveLesson(
                    id=hit.id if isinstance(hit.id, str) else str(hit.id),
                    rule=p.get("rule", ""),
                    confidence=p.get("confidence", 0.0),
                    effectiveness=p.get("effectiveness", 0.0),
                    source_candidates=json.loads(p.get("source_candidates", "[]")),
                    version=p.get("version", 1),
                    created_at=p.get("created_at", ""),
                    last_verified_at=p.get("last_verified_at", ""),
                    superseded_by=p.get("superseded_by") or None,
                )
            )
        return lessons

    # -- Governance --

    async def dedupe(self) -> PruningReport:
        """Find and merge near-duplicate lessons. Keeps the newer/higher-confidence one."""
        lessons = await self.get_all()
        removed = []

        for i, a in enumerate(lessons):
            if a.id in removed or a.superseded_by:
                continue
            for b in lessons[i + 1 :]:
                if b.id in removed or b.superseded_by:
                    continue
                sim = _jaccard_tokens(a.rule, b.rule)
                if sim >= DEDUPE_SIMILARITY_THRESHOLD:
                    # Keep the one with higher effectiveness
                    if a.effectiveness >= b.effectiveness:
                        b.superseded_by = a.id
                        a.source_candidates = list(
                            set(a.source_candidates + b.source_candidates)
                        )
                        await self.store(a)
                        await self.store(b)  # update superseded_by
                        removed.append(b.id)
                    else:
                        a.superseded_by = b.id
                        b.source_candidates = list(
                            set(a.source_candidates + b.source_candidates)
                        )
                        await self.store(b)
                        await self.store(a)
                        removed.append(a.id)
                        break

        return PruningReport(removed=removed, reason="dedupe")

    async def detect_conflicts(self) -> list[Conflict]:
        """Find contradictory rules in the active set."""
        lessons = await self.get_all()
        conflicts = []

        for i, a in enumerate(lessons):
            if a.superseded_by:
                continue
            for b in lessons[i + 1 :]:
                if b.superseded_by:
                    continue
                sim = _jaccard_tokens(a.rule, b.rule)
                if sim >= CONFLICT_SIMILARITY_THRESHOLD or _is_contradiction(
                    a.rule, b.rule
                ):
                    conflicts.append(
                        Conflict(
                            lesson_a_id=a.id,
                            lesson_b_id=b.id,
                            reason=f"similarity={sim:.2f} contradiction={_is_contradiction(a.rule, b.rule)}",
                            similarity=sim,
                        )
                    )
        return conflicts

    async def enforce_budget(self, max_rules: int = MAX_RULES) -> PruningReport:
        """Trim to max_rules by effectiveness × recency. Removes oldest/least effective."""
        lessons = await self.get_all()
        active = [l for l in lessons if not l.superseded_by]

        if len(active) <= max_rules:
            return PruningReport(removed=[], reason="within_budget")

        # Sort by effectiveness * recency
        now = time.time()
        def _score(l: ActiveLesson) -> float:
            try:
                ts = time.mktime(time.strptime(l.created_at, "%Y-%m-%dT%H:%M:%SZ"))
                age_days = max(1, (now - ts) / 86400)
                recency = 1.0 / age_days
            except (ValueError, OSError):
                recency = 0.5
            return l.effectiveness * 0.7 + recency * 0.3

        active.sort(key=_score, reverse=True)
        to_remove = active[max_rules:]
        removed = []

        for lesson in to_remove:
            await self.remove(lesson.id)
            removed.append(lesson.id)

        return PruningReport(removed=removed, reason="budget_enforcement")

    async def get_stats(self) -> dict:
        """Return stats about the active lesson set."""
        lessons = await self.get_all()
        active = [l for l in lessons if not l.superseded_by]
        return {
            "total": len(lessons),
            "active": len(active),
            "superseded": len(lessons) - len(active),
            "avg_confidence": (
                sum(l.confidence for l in active) / len(active) if active else 0
            ),
            "avg_effectiveness": (
                sum(l.effectiveness for l in active) / len(active) if active else 0
            ),
            "total_tokens": sum(l.tokens for l in active),
            "budget_used_pct": (
                sum(l.tokens for l in active) / MAX_ACTIVE_TOKENS * 100
            ),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager: LessonManager | None = None


def get_lesson_manager() -> LessonManager:
    global _manager
    if _manager is None:
        _manager = LessonManager()
    return _manager
