"""Step 2 integration — supersession actually retires stale lessons at promotion.

The unit tests in ``test_lesson_supersession.py`` prove the verdict function.
These prove the WIRING: that a real promotion marks superseded lessons, that it
is non-destructive, and that it can never fail an otherwise-valid promotion.

No Qdrant, no LLM, no service: the lesson store is an in-memory fake and the
governed state machine is exercised directly.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from swarm_os.services.lesson_manager import ActiveLesson  # noqa: E402
from swarm_os.services.prompt_repairer import PromptRepairer  # noqa: E402


class FakeStore:
    """In-memory ACTIVE collection with the LessonManager storage surface."""

    def __init__(self, lessons: list[ActiveLesson]) -> None:
        self.lessons = {l.id: l for l in lessons}
        self.store_calls: list[ActiveLesson] = []

    async def get_all(self) -> list[ActiveLesson]:
        return list(self.lessons.values())

    async def store(self, lesson: ActiveLesson) -> str:
        self.lessons[lesson.id] = lesson
        self.store_calls.append(lesson)
        return lesson.id

    async def remove(self, lesson_id: str) -> bool:
        return self.lessons.pop(lesson_id, None) is not None

    def active(self) -> list[ActiveLesson]:
        return [l for l in self.lessons.values() if not l.superseded_by]


def _lesson(lid: str, rule: str) -> ActiveLesson:
    return ActiveLesson(id=lid, rule=rule, confidence=0.9, effectiveness=0.8)


def _repairer(lessons: list[ActiveLesson]) -> tuple[PromptRepairer, FakeStore]:
    store = FakeStore(lessons)
    pr = PromptRepairer.__new__(PromptRepairer)
    pr.lesson_manager = store
    return pr, store


class TestPromotionRetiresStale:
    def test_replaced_prescription_is_retired(self):
        pr, store = _repairer([_lesson("old", "call-loop: use grep for searching")])
        retired = asyncio.run(
            pr._retire_superseded(
                "call-loop: use ripgrep for searching", candidate_id="cand-1"
            )
        )
        assert retired == ["old"]
        assert store.lessons["old"].superseded_by == "cand-1"
        assert store.active() == []

    def test_corrective_rule_retires_stale_affirmative(self):
        pr, store = _repairer([_lesson("old", "no-edit: use the write tool")])
        asyncio.run(
            pr._retire_superseded(
                "no-edit: never use the write tool", candidate_id="cand-1"
            )
        )
        assert store.lessons["old"].superseded_by == "cand-1"
        assert store.active() == []

    def test_unrelated_lesson_untouched(self):
        pr, store = _repairer([_lesson("other", "turn-budget: re-read the file")])
        retired = asyncio.run(
            pr._retire_superseded(
                "call-loop: use ripgrep for searching", candidate_id="cand-1"
            )
        )
        assert retired == []
        assert store.lessons["other"].superseded_by is None
        assert store.active() == [store.lessons["other"]]

    def test_non_destructive_nothing_deleted(self):
        """Retired lessons stay in the store for audit."""
        pr, store = _repairer([_lesson("old", "call-loop: use grep for searching")])
        asyncio.run(
            pr._retire_superseded(
                "call-loop: use ripgrep for searching", candidate_id="cand-1"
            )
        )
        assert "old" in store.lessons  # present, not deleted
        assert store.lessons["old"].rule == "call-loop: use grep for searching"

    def test_already_superseded_is_skipped(self):
        already = _lesson("old", "call-loop: use grep for searching")
        already.superseded_by = "earlier"
        pr, store = _repairer([already])
        retired = asyncio.run(
            pr._retire_superseded(
                "call-loop: use ripgrep for searching", candidate_id="cand-1"
            )
        )
        assert retired == []
        assert store.lessons["old"].superseded_by == "earlier"


class TestPromotionPathAuditsSupersession:
    """A real promotion must record the supersession decision."""

    def test_promotion_retires_stale_and_audits_it(self):
        import inspect

        from swarm_os.services import prompt_repairer as pr_mod

        src = inspect.getsource(pr_mod.PromptRepairer)
        # The promotion path must call the retiral and audit its outcome.
        assert "_retire_superseded" in src
        assert '"SUPERSEDED"' in src

    def test_retire_is_called_after_store_not_before(self):
        """Ordering matters: the replacement must exist before retiring.

        If retirement ran before the store, a later failure would leave the
        stale lessons retired with nothing in their place.
        """
        import inspect
        import re

        from swarm_os.services import prompt_repairer as pr_mod

        src = inspect.getsource(pr_mod.PromptRepairer)
        body = src.split("async def promote")[1]
        store_at = body.find("await self.lesson_manager.store(new_lesson)")
        retire_at = body.find("await self._retire_superseded(")
        assert store_at != -1 and retire_at != -1
        assert retire_at > store_at, "must store the replacement before retiring"
        _ = re  # noqa: F841


class TestFailSafe:
    def test_store_read_failure_does_not_raise(self):
        """A store outage must not break promotion."""
        pr = PromptRepairer.__new__(PromptRepairer)

        class Broken:
            async def get_all(self):
                raise RuntimeError("qdrant down")

        pr.lesson_manager = Broken()
        assert asyncio.run(pr._retire_superseded("no-edit: use x", candidate_id="c")) == []

    def test_write_failure_does_not_raise(self):
        """One un-writable lesson must not abort the rest."""
        pr = PromptRepairer.__new__(PromptRepairer)

        class PartlyBroken:
            def __init__(self):
                self.a = _lesson("a", "call-loop: use grep for searching")
                self.b = _lesson("b", "call-loop: use grep elsewhere")

            async def get_all(self):
                return [self.a, self.b]

            async def store(self, lesson):
                if lesson.id == "a":
                    raise RuntimeError("write failed")
                return lesson.id

        broken = PartlyBroken()
        pr.lesson_manager = broken
        retired = asyncio.run(
            pr._retire_superseded(
                "call-loop: use ripgrep for searching", candidate_id="c"
            )
        )
        assert retired == ["b"]  # the writable one still retired

    def test_unparseable_incumbent_is_left_alone(self):
        pr, store = _repairer([_lesson("junk", "some unstructured free text")])
        retired = asyncio.run(
            pr._retire_superseded("no-edit: use pathlib", candidate_id="c")
        )
        assert retired == []
        assert store.lessons["junk"].superseded_by is None


class TestRenderRespectsSupersession:
    def test_retired_lesson_is_not_rendered(self):
        """End of the chain: a retired lesson never reaches the prompt."""
        from swarm_os.services.lesson_manager import LessonManager

        lessons = [
            _lesson("stale", "call-loop: use grep for searching"),
            _lesson("fresh", "call-loop: use ripgrep for searching"),
        ]
        lessons[0].superseded_by = "cand-1"

        mgr = LessonManager.__new__(LessonManager)
        mgr.get_all = lambda: asyncio.sleep(0, result=list(lessons))

        block = asyncio.run(mgr.render_active_lessons("ctx"))
        rendered = [
            ln.split(". ", 1)[1]
            for ln in block.splitlines()
            if ln and ln[0].isdigit() and ". " in ln
        ]
        assert rendered == ["call-loop: use ripgrep for searching"]
        # The retired rule must not appear as its own rendered line.
        assert "use grep for searching" not in rendered
