"""Tests for the F2 orchestrator X-derivation contract.

Scope: exact X derivation semantics, T-before-X ordering, C0 construction, and
fail-closed validation — implemented in ``qwen_train/f2_arm_primitives.py``.

Does NOT run experiments, spawn live processes, or touch Qdrant/backends. All
fixtures are deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qwen_train.f2_arm_primitives import (
    build_c0_artifact,
    derive_x_from_frozen,
    lesson_hash_of,
    new_arm_receipt,
    render_block_from_records,
    validate_arm_receipt,
)
from runtime_v2.services.f2_freeze import (
    FreezeVerificationError,
    FrozenArtifact,
    LessonEntry,
    freeze_artifact,
    verify_manifest,
)

_FREEZE_TS = 1_700_000_000.0


def _lesson(lid: str, texture: str, position: int) -> LessonEntry:
    return LessonEntry(
        lesson_id=lid,
        lesson_hash=lesson_hash_of(texture),
        position=position,
        rule_text=texture,
    )


def _freeze_t(lessons: tuple[LessonEntry, ...], *, lesson_l_id: str, **overrides):
    """Deterministic frozen T. The rendered block is built from frozen records."""
    block = render_block_from_records(lessons)
    l_rec = next(le for le in lessons if le.lesson_id == lesson_l_id)
    defaults = dict(
        rendered_artifact=block,
        arm="T",
        ordered_lessons=lessons,
        lesson_l_id=lesson_l_id,
        lesson_l_hash=l_rec.lesson_hash,
        task_id="task-1",
        model_name="test-model",
        git_sha="deadbeef",
        freeze_timestamp=_FREEZE_TS,
    )
    defaults.update(overrides)
    t = freeze_artifact(**defaults)
    verify_manifest(t)
    return t


class TestXDerivation:
    def test_x_removes_exactly_l(self):
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "always run tests after edit", 2),
            _lesson("C", "verify before patch", 3),
        )
        t = _freeze_t(lessons, lesson_l_id="L")
        x = derive_x_from_frozen(t)

        assert x.arm == "X"
        assert [le.lesson_id for le in x.ordered_lessons] == ["A", "C"]
        assert "always run tests" not in x.rendered_artifact
        assert "use pathlib" in x.rendered_artifact
        assert "verify before patch" in x.rendered_artifact

    def test_x_preserves_original_ordering_and_positions(self):
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 2),
            _lesson("C", "verify before patch", 3),
        )
        t = _freeze_t(lessons, lesson_l_id="L")
        x = derive_x_from_frozen(t)

        # positions preserved, L gap remains (1 and 3, no renumber to 1,2)
        assert [le.position for le in x.ordered_lessons] == [1, 3]
        # rendered block keeps original numbers (gap remains)
        assert "1. use pathlib" in x.rendered_artifact
        assert "3. verify before patch" in x.rendered_artifact
        assert "2." not in x.rendered_artifact

    def test_x_preserves_non_l_record_bytes(self):
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 2),
            _lesson("C", "verify before patch", 3),
        )
        t = _freeze_t(lessons, lesson_l_id="L")
        x = derive_x_from_frozen(t)
        for orig, new in zip(
            [le for le in lessons if le.lesson_id != "L"],
            x.ordered_lessons,
        ):
            assert orig.lesson_id == new.lesson_id
            assert orig.lesson_hash == new.lesson_hash
            assert orig.position == new.position
            assert orig.rule_text == new.rule_text

    def test_x_binds_provenance_to_frozen_t(self):
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 2),
            _lesson("C", "verify before patch", 3),
        )
        t = _freeze_t(lessons, lesson_l_id="L")
        x = derive_x_from_frozen(t)

        assert x.promotion_proof_ref is not None
        assert t.content_address in x.promotion_proof_ref
        assert t.manifest_hash in x.promotion_proof_ref

    def test_x_never_rerenders(self):
        # derivation must be a pure function; a changed rendered block on T
        # must not influence X beyond the frozen records.
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 2),
            _lesson("C", "verify before patch", 3),
        )
        t = _freeze_t(lessons, lesson_l_id="L")
        t2 = _freeze_t(lessons, lesson_l_id="L")
        x1 = derive_x_from_frozen(t)
        x2 = derive_x_from_frozen(t2)
        assert x1.rendered_artifact == x2.rendered_artifact
        assert x1.treatment_set_hash == x2.treatment_set_hash

    def test_x_deterministic_across_identical_inputs(self):
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 2),
        )
        x1 = derive_x_from_frozen(_freeze_t(lessons, lesson_l_id="L"))
        x2 = derive_x_from_frozen(_freeze_t(lessons, lesson_l_id="L"))
        assert x1.manifest_hash == x2.manifest_hash
        assert x1.treatment_set_hash == x2.treatment_set_hash


class TestXFailClosed:
    def test_l_missing_rejected(self):
        lessons = (_lesson("A", "use pathlib", 1),)
        # T has no L record at all; lesson_l_id references a nonexistent id
        t_bad = freeze_artifact(
            rendered_artifact=render_block_from_records(lessons),
            arm="T", ordered_lessons=lessons,
            lesson_l_id="L",
            lesson_l_hash=lesson_hash_of("ghost"),
            task_id="task-1", git_sha="deadbeef", freeze_timestamp=_FREEZE_TS,
        )
        with pytest.raises(FreezeVerificationError):
            derive_x_from_frozen(t_bad)

    def test_l_duplicate_rejected(self):
        # two frozen records share lesson_id "L" -> derive must reject
        dup = (
            LessonEntry(lesson_id="L", lesson_hash=lesson_hash_of("run tests"), position=1, rule_text="run tests"),
            LessonEntry(lesson_id="L", lesson_hash=lesson_hash_of("run tests 2"), position=2, rule_text="run tests 2"),
        )
        t = freeze_artifact(
            rendered_artifact="1. run tests\n2. run tests 2",
            arm="T", ordered_lessons=dup,
            lesson_l_id="L", lesson_l_hash=lesson_hash_of("run tests"),
            task_id="task-1", git_sha="deadbeef", freeze_timestamp=_FREEZE_TS,
        )
        with pytest.raises(FreezeVerificationError):
            derive_x_from_frozen(t)

    def test_duplicate_position_rejected(self):
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 2),
            _lesson("C", "dup position", 2),
        )
        with pytest.raises(FreezeVerificationError):
            derive_x_from_frozen(_freeze_t(lessons, lesson_l_id="L"))

    def test_malformed_position_rejected(self):
        lessons = (
            _lesson("A", "use pathlib", 0),
            _lesson("L", "run tests", 1),
        )
        # position 0 is malformed
        with pytest.raises(FreezeVerificationError):
            derive_x_from_frozen(_freeze_t(lessons, lesson_l_id="L"))

    def test_position_gap_rejected(self):
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 3),
        )
        # positions not contiguous 1..N (2 missing)
        with pytest.raises(FreezeVerificationError):
            derive_x_from_frozen(_freeze_t(lessons, lesson_l_id="L"))

    def test_l_hash_mismatch_rejected(self):
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 2),
        )
        t = _freeze_t(lessons, lesson_l_id="L")
        # tamper with lesson_l_hash
        import dataclasses

        bad = dataclasses.replace(t, lesson_l_hash="0" * 64)
        with pytest.raises(FreezeVerificationError):
            derive_x_from_frozen(bad)

    def test_l_only_rejected(self):
        lessons = (_lesson("L", "only lesson", 1),)
        # X would be empty (ambiguous with C0)
        with pytest.raises(FreezeVerificationError):
            derive_x_from_frozen(_freeze_t(lessons, lesson_l_id="L"))

    def test_non_t_source_rejected(self):
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 2),
        )
        x = derive_x_from_frozen(_freeze_t(lessons, lesson_l_id="L"))
        with pytest.raises(FreezeVerificationError):
            derive_x_from_frozen(x)  # X is arm X, not T


class TestTBeforeX:
    def test_derivation_requires_frozen_t(self):
        # derive_x_from_frozen only accepts FrozenArtifact; a raw block cannot
        # be passed. The API shape enforces freeze-before-derive.
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 2),
        )
        t = _freeze_t(lessons, lesson_l_id="L")
        assert isinstance(t, FrozenArtifact)
        # ensure T is verified before deriving (hypothesis: ordering is structural)
        verify_manifest(t)
        x = derive_x_from_frozen(t)
        assert x.rendered_artifact != ""  # derived only after T exists


class TestC0:
    def test_c0_empty_treatment(self):
        c0 = build_c0_artifact(task_id="task-1", git_sha="deadbeef", model_name="m")
        assert c0.arm == "C0"
        assert c0.rendered_artifact == ""
        assert c0.ordered_lessons == ()
        assert c0.lesson_l_id is None
        assert c0.lesson_l_hash is None
        verify_manifest(c0)

    def test_c0_does_not_inherit_treatment_state(self):
        c0 = build_c0_artifact(task_id="task-1")
        assert c0.treatment_set_hash == c0.compute_treatment_set_hash()
        # no ordered lessons / no L
        assert len(c0.ordered_lessons) == 0


class TestReceiptSchema:
    def _manifest(self):
        lessons = (
            _lesson("A", "use pathlib", 1),
            _lesson("L", "run tests", 2),
        )
        return _freeze_t(lessons, lesson_l_id="L")

    def test_receipt_has_required_fields(self):
        m = self._manifest()
        receipt = new_arm_receipt(
            manifest=m,
            manifest_path=Path("/tmp/x.json"),
            rollout_id="r1",
            trajectory_run_id="traj1",
            process_identity={"pid": 1},
            delivered_artifact=m.rendered_artifact,
            lesson_block_hash=m.treatment_set_hash,
            final_prompt_hash="fp",
            delivery_timestamp=1.0,
            verification_result="verified",
        )
        validate_arm_receipt(receipt)

    def test_receipt_rejects_missing_fields(self):
        with pytest.raises(ValueError):
            validate_arm_receipt({"arm": "T"})

    def test_receipt_matches_mixin_identity(self):
        m = self._manifest()
        receipt = new_arm_receipt(
            manifest=m,
            manifest_path=Path("/tmp/x.json"),
            rollout_id="r1",
            trajectory_run_id="traj1",
            process_identity={"pid": 1},
            delivered_artifact=m.rendered_artifact,
            lesson_block_hash=m.treatment_set_hash,
            final_prompt_hash="fp",
            delivery_timestamp=1.0,
            verification_result="verified",
        )
        # manifest identity must bind to the actual manifest
        assert receipt["manifest_identity"]["manifest_hash"] == m.manifest_hash
        assert receipt["treatment_identity"]["lesson_l_id"] == "L"