"""F2 Freeze Artifact Primitives — Phase 1 Tests.

Covers every verification target from the task specification:
A  Determinism
B  Artifact mutation
C  Treatment identity (F0-conformant: treatment_set_hash = SHA256(rendered_artifact))
D  Manifest determinism
E  Self-hash exclusion
F  Round-trip
G  Corruption
H  Manifest corruption
I  Provenance mismatch
J  Unsupported schema
K  No fallback
L  Atomic persistence
M  Hash contract (F0 conformance)
N  T/X hash distinction
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from swarm_os.services.f2_freeze import (
    FreezeVerificationError,
    FrozenArtifact,
    LessonEntry,
    dict_to_manifest,
    freeze_artifact,
    load_manifest,
    manifest_to_dict,
    persist_manifest,
    serialize_manifest,
    verify_manifest,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_lessons() -> tuple[LessonEntry, ...]:
    return (
        LessonEntry(lesson_id="aaa", lesson_hash="h_aaa", position=1, rule_text="Always use pathlib"),
        LessonEntry(lesson_id="bbb", lesson_hash="h_bbb", position=2, rule_text="Verify before patching"),
        LessonEntry(lesson_id="ccc", lesson_hash="h_ccc", position=3, rule_text="Run tests after edits"),
    )


def _make_frozen(lessons: tuple[LessonEntry, ...] | None = None, rendered_artifact: str | None = None, **overrides) -> FrozenArtifact:
    return freeze_artifact(
        rendered_artifact=rendered_artifact if rendered_artifact is not None else "1. Always use pathlib\n2. Verify before patching\n3. Run tests after edits",
        arm=overrides.pop("arm", "T"),
        ordered_lessons=lessons or _make_lessons(),
        lesson_l_id=overrides.pop("lesson_l_id", "bbb"),
        lesson_l_hash=overrides.pop("lesson_l_hash", "h_bbb"),
        git_sha=overrides.pop("git_sha", "abc123def456"),
        model_name=overrides.pop("model_name", "robs4b"),
        task_id=overrides.pop("task_id", "pypa__twine-1066"),
        freeze_timestamp=overrides.pop("freeze_timestamp", 1700000000.0),
        **overrides,
    )


# ---------------------------------------------------------------------------
# A. Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_treatment_set_hash(self):
        a = _make_frozen()
        b = _make_frozen()
        assert a.treatment_set_hash == b.treatment_set_hash

    def test_same_inputs_same_lesson_set_hash(self):
        a = _make_frozen()
        b = _make_frozen()
        assert a.lesson_set_hash == b.lesson_set_hash

    def test_same_inputs_same_manifest_hash(self):
        a = _make_frozen()
        b = _make_frozen()
        assert a.manifest_hash == b.manifest_hash

    def test_same_inputs_same_canonical_bytes(self):
        a = _make_frozen()
        b = _make_frozen()
        assert a.canonical_bytes() == b.canonical_bytes()


# ---------------------------------------------------------------------------
# B. Artifact mutation
# ---------------------------------------------------------------------------

class TestArtifactMutation:
    def test_one_byte_change_alters_treatment_set_hash(self):
        original = _make_frozen()
        mutated = _make_frozen(rendered_artifact="1. Always use pathlib\n2. Verify before patching\n3. Run tests after EDITS")
        assert original.treatment_set_hash != mutated.treatment_set_hash

    def test_one_byte_change_alters_manifest_hash(self):
        original = _make_frozen()
        mutated = _make_frozen(rendered_artifact="X")
        assert original.manifest_hash != mutated.manifest_hash

    def test_one_byte_change_preserves_lesson_set_hash(self):
        original = _make_frozen()
        mutated = _make_frozen(rendered_artifact="completely different text")
        assert original.lesson_set_hash == mutated.lesson_set_hash


# ---------------------------------------------------------------------------
# C. Treatment identity (F0-conformant)
# ---------------------------------------------------------------------------

class TestTreatmentIdentity:
    def test_same_lessons_same_lesson_set_hash(self):
        lessons = _make_lessons()
        a = _make_frozen(lessons=lessons)
        b = _make_frozen(lessons=lessons)
        assert a.lesson_set_hash == b.lesson_set_hash

    def test_different_lessons_different_lesson_set_hash(self):
        a = _make_frozen(lessons=_make_lessons())
        other = (LessonEntry(lesson_id="xxx", lesson_hash="h_xxx", position=1),)
        b = _make_frozen(lessons=other)
        assert a.lesson_set_hash != b.lesson_set_hash

    def test_different_arm_different_lesson_set_hash(self):
        a = _make_frozen(arm="T")
        b = _make_frozen(arm="X")
        assert a.lesson_set_hash != b.lesson_set_hash

    def test_different_protocol_different_lesson_set_hash(self):
        a = _make_frozen(protocol_version="f2_v1")
        b = _make_frozen(protocol_version="f2_v2")
        assert a.lesson_set_hash != b.lesson_set_hash

    def test_lesson_order_invariant_for_lesson_set_hash(self):
        lessons_a = (
            LessonEntry(lesson_id="aaa", lesson_hash="h1", position=1),
            LessonEntry(lesson_id="bbb", lesson_hash="h2", position=2),
        )
        lessons_b = (
            LessonEntry(lesson_id="bbb", lesson_hash="h2", position=2),
            LessonEntry(lesson_id="aaa", lesson_hash="h1", position=1),
        )
        a = _make_frozen(lessons=lessons_a)
        b = _make_frozen(lessons=lessons_b)
        assert a.lesson_set_hash == b.lesson_set_hash


# ---------------------------------------------------------------------------
# D. Manifest determinism
# ---------------------------------------------------------------------------

class TestManifestDeterminism:
    def test_same_content_same_manifest_hash(self):
        a = _make_frozen()
        b = _make_frozen()
        assert serialize_manifest(a) == serialize_manifest(b)

    def test_canonical_json_is_sorted(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        keys = list(d.keys())
        assert keys == sorted(keys)

    def test_no_whitespace_in_canonical(self):
        m = _make_frozen()
        raw = serialize_manifest(m).decode("utf-8")
        assert "\n" not in raw
        assert ": " not in raw


# ---------------------------------------------------------------------------
# E. Self-hash exclusion
# ---------------------------------------------------------------------------

class TestSelfHashExclusion:
    def test_manifest_hash_not_in_hash_input(self):
        m = _make_frozen()
        canonical_without = m.canonical_bytes(include_manifest_hash=False)
        canonical_with = m.canonical_bytes(include_manifest_hash=True)
        assert canonical_without != canonical_with

    def test_changing_manifest_hash_field_alters_stored_hash(self):
        m = _make_frozen()
        original_manifest = m.manifest_hash
        d = manifest_to_dict(m)
        d["manifest_hash"] = "0" * 64
        corrupted = dict_to_manifest(d)
        recomputed = corrupted.compute_manifest_hash()
        assert recomputed == original_manifest
        assert recomputed != corrupted.manifest_hash

    def test_self_hash_exclusion_roundtrip(self):
        m = _make_frozen()
        recomputed = m.compute_manifest_hash()
        assert recomputed == m.manifest_hash

    def test_json_roundtrip_preserves_manifest_hash(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        raw = json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        d2 = json.loads(raw)
        assert d == d2
        m2 = dict_to_manifest(d2)
        assert m2.manifest_hash == m.manifest_hash


# ---------------------------------------------------------------------------
# F. Round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_dict_roundtrip(self):
        original = _make_frozen()
        d = manifest_to_dict(original)
        restored = dict_to_manifest(d)
        assert restored == original

    def test_persist_and_load(self, tmp_path: Path):
        original = _make_frozen()
        path = persist_manifest(original, tmp_path)
        loaded = load_manifest(path)
        assert loaded == original
        assert loaded.manifest_hash == original.manifest_hash

    def test_persist_file_exists(self, tmp_path: Path):
        m = _make_frozen()
        path = persist_manifest(m, tmp_path)
        assert path.exists()
        assert path.name.startswith(m.content_address)
        assert path.name.endswith("_f2_freeze.json")

    def test_persist_valid_json(self, tmp_path: Path):
        m = _make_frozen()
        path = persist_manifest(m, tmp_path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["manifest_hash"] == m.manifest_hash
        assert data["treatment_set_hash"] == m.treatment_set_hash


# ---------------------------------------------------------------------------
# G. Corruption
# ---------------------------------------------------------------------------

class TestCorruption:
    def test_artifact_content_mismatch_fails_verify(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["rendered_artifact"] = "corrupted"
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError, match="Treatment set hash mismatch"):
            verify_manifest(corrupted)

    def test_treatment_set_hash_mismatch_fails_verify(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["treatment_set_hash"] = "0" * 64
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError, match="Treatment set hash mismatch"):
            verify_manifest(corrupted)


# ---------------------------------------------------------------------------
# H. Manifest corruption
# ---------------------------------------------------------------------------

class TestManifestCorruption:
    def test_manifest_hash_mismatch_fails_verify(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["manifest_hash"] = "0" * 64
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError, match="Manifest hash mismatch"):
            verify_manifest(corrupted)

    def test_lesson_set_hash_mismatch_fails_verify(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["lesson_set_hash"] = "0" * 64
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError, match="Lesson set hash mismatch"):
            verify_manifest(corrupted)


# ---------------------------------------------------------------------------
# I. Provenance mismatch
# ---------------------------------------------------------------------------

class TestProvenanceMismatch:
    def test_git_sha_change_in_manifest_detected(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["git_sha"] = "DEADBEEF"
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError, match="Manifest hash mismatch"):
            verify_manifest(corrupted)

    def test_model_name_change_in_manifest_detected(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["model_name"] = "wrong-model"
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError, match="Manifest hash mismatch"):
            verify_manifest(corrupted)

    def test_task_id_change_in_manifest_detected(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["task_id"] = "wrong-task"
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError, match="Manifest hash mismatch"):
            verify_manifest(corrupted)

    def test_arm_change_in_manifest_detected(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["arm"] = "X"
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError):
            verify_manifest(corrupted)

    def test_lesson_l_id_not_in_lessons_fails(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["lesson_l_id"] = "nonexistent"
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError):
            verify_manifest(corrupted)


# ---------------------------------------------------------------------------
# J. Unsupported schema
# ---------------------------------------------------------------------------

class TestUnsupportedSchema:
    def test_unsupported_schema_version_fails(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["schema_version"] = 999
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError, match="Unsupported schema version"):
            verify_manifest(corrupted)

    def test_schema_version_zero_fails(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["schema_version"] = 0
        corrupted = dict_to_manifest(d)
        with pytest.raises(FreezeVerificationError, match="Unsupported schema version"):
            verify_manifest(corrupted)


# ---------------------------------------------------------------------------
# K. No fallback
# ---------------------------------------------------------------------------

class TestNoFallback:
    def test_verification_failure_raises_explicit_error(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["treatment_set_hash"] = "bad"
        corrupted = dict_to_manifest(d)
        try:
            verify_manifest(corrupted)
            pytest.fail("Expected FreezeVerificationError")
        except FreezeVerificationError:
            pass

    def test_verification_error_is_not_caught_by_broad_except(self):
        m = _make_frozen()
        d = manifest_to_dict(m)
        d["manifest_hash"] = "bad"
        corrupted = dict_to_manifest(d)
        caught = False
        try:
            verify_manifest(corrupted)
        except FreezeVerificationError:
            caught = True
        except Exception:
            pytest.fail("Should raise FreezeVerificationError, not broad exception")
        assert caught

    def test_valid_manifest_passes_verify(self):
        m = _make_frozen()
        verify_manifest(m)  # must not raise

    def test_empty_artifact_for_non_c0_arm_fails(self):
        m = _make_frozen(rendered_artifact="", arm="T")
        with pytest.raises(FreezeVerificationError, match="Empty rendered_artifact"):
            verify_manifest(m)

    def test_empty_artifact_for_c0_arm_passes(self):
        m = _make_frozen(rendered_artifact="", arm="C0")
        verify_manifest(m)  # must not raise

    def test_duplicate_lesson_ids_fail(self):
        lessons = (
            LessonEntry(lesson_id="aaa", lesson_hash="h1", position=1),
            LessonEntry(lesson_id="aaa", lesson_hash="h2", position=2),
        )
        m = _make_frozen(lessons=lessons)
        with pytest.raises(FreezeVerificationError, match="Duplicate lesson_id"):
            verify_manifest(m)


# ---------------------------------------------------------------------------
# L. Atomic persistence
# ---------------------------------------------------------------------------

class TestAtomicPersistence:
    def test_atomic_write_succeeds(self, tmp_path: Path):
        m = _make_frozen()
        path = persist_manifest(m, tmp_path)
        assert path.exists()
        loaded = load_manifest(path)
        assert loaded.manifest_hash == m.manifest_hash

    def test_no_partial_files_remain(self, tmp_path: Path):
        m = _make_frozen()
        persist_manifest(m, tmp_path)
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert not files[0].name.startswith(".")

    def test_atomic_write_idempotent(self, tmp_path: Path):
        m = _make_frozen()
        p1 = persist_manifest(m, tmp_path)
        p2 = persist_manifest(m, tmp_path)
        assert p1 == p2
        loaded = load_manifest(p2)
        assert loaded == m

    def test_multiple_freezes_coexist(self, tmp_path: Path):
        m1 = freeze_artifact(
            rendered_artifact="lesson one", arm="T",
            freeze_timestamp=100.0, git_sha="aaa",
        )
        m2 = freeze_artifact(
            rendered_artifact="lesson two", arm="X",
            freeze_timestamp=200.0, git_sha="bbb",
        )
        p1 = persist_manifest(m1, tmp_path)
        p2 = persist_manifest(m2, tmp_path)
        assert p1 != p2
        assert load_manifest(p1) == m1
        assert load_manifest(p2) == m2
        assert load_manifest(p1).manifest_hash != load_manifest(p2).manifest_hash

    def test_directory_created_if_missing(self, tmp_path: Path):
        target = tmp_path / "nested" / "deep"
        m = _make_frozen()
        path = persist_manifest(m, target)
        assert path.exists()
        assert target.is_dir()


# ---------------------------------------------------------------------------
# M. Hash contract — F0 conformance
# ---------------------------------------------------------------------------

class TestHashContractF0:
    def test_treatment_set_hash_is_sha256_of_rendered_artifact(self):
        """F0 §4: treatment_set_hash = SHA256(active_block)."""
        m = _make_frozen()
        expected = hashlib.sha256(m.rendered_artifact.encode("utf-8")).hexdigest()
        assert m.treatment_set_hash == expected

    def test_changing_rendered_artifact_changes_treatment_set_hash(self):
        a = _make_frozen()
        b = _make_frozen(rendered_artifact="different text")
        assert a.treatment_set_hash != b.treatment_set_hash

    def test_changing_rendered_artifact_preserves_lesson_set_hash(self):
        a = _make_frozen()
        b = _make_frozen(rendered_artifact="different text")
        assert a.lesson_set_hash == b.lesson_set_hash

    def test_changing_lesson_membership_changes_lesson_set_hash(self):
        lessons_a = (LessonEntry(lesson_id="aaa", lesson_hash="h1", position=1),)
        lessons_b = (LessonEntry(lesson_id="bbb", lesson_hash="h2", position=1),)
        a = _make_frozen(lessons=lessons_a)
        b = _make_frozen(lessons=lessons_b)
        assert a.lesson_set_hash != b.lesson_set_hash

    def test_changing_arm_changes_lesson_set_hash(self):
        a = _make_frozen(arm="T")
        b = _make_frozen(arm="X")
        assert a.lesson_set_hash != b.lesson_set_hash

    def test_treatment_set_hash_differs_from_lesson_set_hash(self):
        """The two hashes must be distinct — different input, different purpose."""
        m = _make_frozen()
        assert m.treatment_set_hash != m.lesson_set_hash

    def test_lesson_set_hash_includes_arm(self):
        a = _make_frozen(arm="T")
        b = _make_frozen(arm="X")
        assert a.lesson_set_hash != b.lesson_set_hash

    def test_lesson_set_hash_includes_protocol(self):
        a = _make_frozen(protocol_version="f2_v1")
        b = _make_frozen(protocol_version="f2_v2")
        assert a.lesson_set_hash != b.lesson_set_hash


# ---------------------------------------------------------------------------
# N. T/X hash distinction
# ---------------------------------------------------------------------------

class TestTXHashDistinction:
    def test_treatment_hash_changes_with_l_removal(self):
        """Removing L changes the rendered artifact, hence the treatment hash."""
        lessons = (
            LessonEntry(lesson_id="aaa", lesson_hash="h1", position=1, rule_text="Rule A"),
            LessonEntry(lesson_id="bbb", lesson_hash="h2", position=2, rule_text="Rule L"),
            LessonEntry(lesson_id="ccc", lesson_hash="h3", position=3, rule_text="Rule B"),
        )
        t_text = "1. Rule A\n2. Rule L\n3. Rule B"
        x_text = "1. Rule A\n3. Rule B"
        t = _make_frozen(lessons=lessons, rendered_artifact=t_text, arm="T")
        x = _make_frozen(lessons=(lessons[0], lessons[2]), rendered_artifact=x_text, arm="X")

        # treatment_set_hash (F0: SHA256 of rendered text) must differ
        assert t.treatment_set_hash != x.treatment_set_hash
        assert t.treatment_set_hash == hashlib.sha256(t_text.encode("utf-8")).hexdigest()
        assert x.treatment_set_hash == hashlib.sha256(x_text.encode("utf-8")).hexdigest()

    def test_lesson_set_hash_changes_with_l_removal_and_arm(self):
        """Removing L and changing arm both change the lesson-set hash."""
        lessons_t = (
            LessonEntry(lesson_id="aaa", lesson_hash="h1", position=1),
            LessonEntry(lesson_id="bbb", lesson_hash="h2", position=2),
            LessonEntry(lesson_id="ccc", lesson_hash="h3", position=3),
        )
        lessons_x = (lessons_t[0], lessons_t[2])
        t = _make_frozen(lessons=lessons_t, arm="T")
        x = _make_frozen(lessons=lessons_x, arm="X")
        assert t.lesson_set_hash != x.lesson_set_hash

    def test_t_and_x_manifest_hashes_differ(self):
        """Different rendered text + different lesson set → different manifest."""
        t = _make_frozen(rendered_artifact="1. A\n2. L\n3. B", arm="T")
        x = _make_frozen(rendered_artifact="1. A\n3. B", arm="X")
        assert t.manifest_hash != x.manifest_hash

    def test_c0_lesson_set_hash_differs_from_t(self):
        """C0 has different arm and empty lesson set."""
        t = _make_frozen(arm="T")
        c0 = _make_frozen(arm="C0", rendered_artifact="")
        assert t.lesson_set_hash != c0.lesson_set_hash

    def test_c0_treatment_hash_is_sha256_of_empty(self):
        """C0 rendered artifact is empty; treatment hash is SHA256 of empty string."""
        c0 = _make_frozen(arm="C0", rendered_artifact="")
        expected = hashlib.sha256(b"").hexdigest()
        assert c0.treatment_set_hash == expected
