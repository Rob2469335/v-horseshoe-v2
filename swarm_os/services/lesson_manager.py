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
import time
import uuid
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

    async def render_active_lessons(
        self,
        task_context: str = "",
        max_chars: int = 700,
    ) -> str:
        """THE governed seam — render the deterministic [BEHAVIORAL LESSONS]
        block for a Robs worker prompt.

        This is the ONLY production path by which behavioral instruction may
        enter the worker's system prompt. It reads the active lesson set
        (versioned, deduped, budgeted, contradiction-checked by the Promotion
        Gate) and renders the *rule text only* — never raw Qdrant history,
        never the source trajectory, never confidence/evidence prose.

        Fail-closed: any retrieval/embedding error returns "" (no behavioral
        injection at all), and the rendered block is hard-bounded by both
        MAX_ACTIVE_TOKENS and ``max_chars`` even on partial retrieval.
        """
        try:
            lessons = await self.get_all()
        except Exception as exc:  # noqa: BLE001
            _log.warning("render_active_lessons: retrieval failed: %s", exc)
            return ""
        active = [l for l in lessons if not l.superseded_by]
        active.sort(key=lambda l: (max(0.0, l.effectiveness), l.version), reverse=True)
        lines: list[str] = []
        budget_tokens = MAX_ACTIVE_TOKENS
        for lesson in active:
            rule_tokens = estimate_tokens(lesson.rule)
            if rule_tokens > budget_tokens:
                continue  # cannot fit → next (already sorted by value)
            if rule_tokens > MAX_RULE_TOKENS:
                continue  # governance violation — never render an over-budget rule
            lines.append(lesson.rule)
            budget_tokens -= rule_tokens
        if not lines:
            return ""
        block = "\n".join(f"{i}. {r}" for i, r in enumerate(lines, 1))
        if len(block) > max_chars:
            block = block[: max_chars]
        return f"\n\n[BEHAVIORAL LESSONS]\n{block}"

    async def get_all(self) -> list[ActiveLesson]:
        """Return all active lessons (for pruning/conflict checks)."""
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
