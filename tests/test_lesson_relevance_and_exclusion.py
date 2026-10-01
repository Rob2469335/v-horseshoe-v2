"""Steps 3+4 — relevance ordering and exclude_ids on the governed seam.

Two behaviours, both verified against the real production functions:

Step 3 — ``task_context`` now participates in selection. Previously it was a
declared-but-unread parameter and ranking used only ``effectiveness``, a value
written once at promotion and never updated. Ordering is now
``relevance x effectiveness`` with a deterministic governed tie-break.

  * Relevance decides ORDER among lessons that already fit the budget. It
    never decides eligibility. A relevance threshold would make eligibility a
    retrieval question, and on a store containing contradicted lessons
    similarity provably cannot separate stale from current (AUROC 0.59,
    arXiv:2606.26511).
  * An embedder outage degrades to governed order rather than failing closed,
    because a transient outage must not silently withdraw every lesson.

Step 4 — ``exclude_ids`` withholds named lessons from eligibility *before*
packing, so withholding cannot promote a lesson that budget-exclusion had
already excluded. That would shift the delivered set for a reason unrelated to
the exclusion.

No Qdrant, no embedder, no service.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from swarm_os.services.lesson_manager import ActiveLesson, LessonManager  # noqa: E402


class Hit:
    def __init__(self, hid: str, score: float) -> None:
        self.id = hid
        self.score = score


class FakeManager(LessonManager):
    """LessonManager with a fixed store and a controllable embedder/query."""

    def __init__(self, lessons, scores=None, embed_fails=False) -> None:
        self._lessons = list(lessons)
        self._scores = scores or {}
        self._embed_fails = embed_fails
        self.embed_calls = 0
        self.query_calls = 0

    async def get_all(self):
        return list(self._lessons)

    async def _embed(self, text):
        self.embed_calls += 1
        if self._embed_fails:
            raise RuntimeError("embedder down")
        return [0.0] * 768

    async def _client_query_active(self, vector):
        self.query_calls += 1
        return self._scores


def _l(lid, rule, eff=0.9, version=1, superseded_by=None):
    return ActiveLesson(
        id=lid,
        rule=rule,
        confidence=0.9,
        effectiveness=eff,
        version=version,
        superseded_by=superseded_by,
    )


def _render(mgr, ctx="", **kw):
    return asyncio.run(mgr.render_active_lessons_with_records(ctx, **kw))


def _rules(block):
    return [
        ln.split(". ", 1)[1]
        for ln in block.splitlines()
        if ln and ln[0].isdigit() and ". " in ln
    ]


class TestRelevanceOrdering:
    def test_relevance_changes_order(self):
        """A relevant-but-less-effective lesson outranks an irrelevant one."""
        mgr = FakeManager(
            [_l("a", "call-loop: use ripgrep", eff=0.9),
             _l("b", "turn-budget: re-read file", eff=0.8)],
            scores={"a": 0.9, "b": 0.01},
        )
        block, delivered = _render(mgr, ctx="searching with ripgrep")
        assert [d.id for d in delivered][0] == "a"
        assert _rules(block)[0] == "call-loop: use ripgrep"

    def test_zero_relevance_falls_back_to_governed_order(self):
        """No similarity signal → the static effectiveness order."""
        mgr = FakeManager(
            [_l("low", "no-edit: use pathlib", eff=0.2),
             _l("high", "call-loop: use ripgrep", eff=0.95)],
            scores={},
        )
        block, _ = _render(mgr, ctx="anything")
        assert _rules(block)[0] == "call-loop: use ripgrep"

    def test_embedder_failure_degrades_not_fails_closed(self):
        """A transient embedder outage must not withdraw every lesson."""
        mgr = FakeManager([_l("a", "no-edit: use pathlib")], embed_fails=True)
        block, delivered = _render(mgr, ctx="some query")
        assert block != ""
        assert [d.id for d in delivered] == ["a"]

    def test_blank_context_does_not_embed(self):
        mgr = FakeManager([_l("a", "no-edit: use pathlib")])
        _render(mgr, ctx="   ")
        assert mgr.embed_calls == 0

    def test_ties_are_deterministic(self):
        """Equal scores must always render in the same order."""
        lessons = [_l("z", "no-edit: use z", eff=0.5), _l("a", "no-edit: use a", eff=0.5)]
        scores = {"z": 0.7, "a": 0.7}
        outs = set()
        for _ in range(8):
            mgr = FakeManager(lessons, scores=scores)
            b, _ = _render(mgr, ctx="q")
            outs.add(tuple(_rules(b)))
        assert len(outs) == 1

    def test_superseded_never_ranked(self):
        mgr = FakeManager(
            [_l("stale", "call-loop: use grep", eff=0.99, superseded_by="c1"),
             _l("fresh", "call-loop: use ripgrep", eff=0.5)],
            scores={"stale": 1.0, "fresh": 0.0},
        )
        block, delivered = _render(mgr, ctx="searching")
        assert [d.id for d in delivered] == ["fresh"]
        assert "use grep" not in _rules(block)


class TestExcludeIds:
    def test_named_lesson_withheld(self):
        mgr = FakeManager([_l("keep", "no-edit: use pathlib"), _l("drop", "call-loop: use grep")])
        block, delivered = _render(mgr, ctx="q", exclude_ids=["drop"])
        assert [d.id for d in delivered] == ["keep"]
        assert _rules(block) == ["no-edit: use pathlib"]

    def test_excluding_everything_renders_nothing(self):
        mgr = FakeManager([_l("a", "no-edit: use pathlib")])
        block, delivered = _render(mgr, ctx="q", exclude_ids=["a"])
        assert block == ""
        assert delivered == []

    def test_exclusion_does_not_renumber_survivors(self):
        mgr = FakeManager(
            [_l("a", "no-edit: use pathlib"), _l("b", "call-loop: use grep")]
        )
        block, delivered = _render(mgr, ctx="q", exclude_ids=["a"])
        assert [d.id for d in delivered] == ["b"]
        assert _rules(block) == ["call-loop: use grep"]

    def test_unknown_id_is_ignored(self):
        mgr = FakeManager([_l("a", "no-edit: use pathlib")])
        block, delivered = _render(mgr, ctx="q", exclude_ids=["does-not-exist"])
        assert [d.id for d in delivered] == ["a"]

    def test_exclusion_does_not_promote_budget_excluded_lesson(self):
        """Withholding must not change the delivered set for another reason.

        'big' does not fit MAX_ACTIVE_TOKENS, so it is budget-excluded. Removing
        a different lesson must NOT pull 'big' into the prompt.
        """
        big = _l("big", "call-loop: " + ("do the thing " * 40), eff=0.99)
        mgr = FakeManager([_l("a", "no-edit: use pathlib"), big])
        baseline, _ = _render(mgr, ctx="q")
        assert _rules(baseline) == ["no-edit: use pathlib"]

        after, delivered = _render(mgr, ctx="q", exclude_ids=["a"])
        assert after == ""  # nothing pulled in
        assert delivered == []

    def test_exclude_ids_accepts_any_iterable(self):
        mgr = FakeManager([_l("a", "no-edit: use pathlib"), _l("b", "call-loop: use grep")])
        block, delivered = _render(mgr, ctx="q", exclude_ids=("b",))
        assert [d.id for d in delivered] == ["a"]

    def test_public_render_signature_accepts_exclude_ids(self):
        mgr = FakeManager([_l("a", "no-edit: use pathlib"), _l("b", "call-loop: use grep")])
        block = asyncio.run(
            mgr.render_active_lessons("q", exclude_ids=["b"])
        )
        assert "use grep" not in block
        assert "use pathlib" in block

    def test_frozen_derivation_unaffected(self):
        """derive_x_from_frozen must NOT use exclude_ids or reranking."""
        from qwen_train.f2_arm_primitives import derive_x_from_frozen
        from runtime_v2.services.f2_freeze import LessonEntry, freeze_artifact
        from qwen_train.f2_arm_primitives import lesson_hash_of

        entries = (
            LessonEntry(lesson_id="M", lesson_hash=lesson_hash_of("use grep"),
                        position=1, rule_text="use grep"),
            LessonEntry(lesson_id="L", lesson_hash=lesson_hash_of("use pathlib"),
                        position=2, rule_text="use pathlib"),
        )
        l_hash = lesson_hash_of("use pathlib")
        entries = (
            LessonEntry(lesson_id="M", lesson_hash=lesson_hash_of("use grep"),
                        position=1, rule_text="use grep"),
            LessonEntry(lesson_id="L", lesson_hash=l_hash,
                        position=2, rule_text="use pathlib"),
        )
        art = freeze_artifact(
            rendered_artifact="use grep\nuse pathlib", arm="T",
            ordered_lessons=entries, lesson_l_id="L",
            lesson_l_hash=l_hash, task_id="t", git_sha="s",
            model_name="m", freeze_timestamp=1.0,
        )
        x = derive_x_from_frozen(art)
        # L removed; M survives at its ORIGINAL position with a gap left behind.
        assert [e.lesson_id for e in x.ordered_lessons] == ["M"]
        assert [e.position for e in x.ordered_lessons] == [1]
        # X drops the L identity: the lesson is no longer present, so X carries no
        # lesson_l_id and must not claim one.
        assert x.lesson_l_id is None
        # The surviving record is byte-identical to the frozen original.
        assert x.ordered_lessons[0] == entries[0]
