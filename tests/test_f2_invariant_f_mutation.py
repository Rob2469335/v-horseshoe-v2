"""Invariant F mutation coverage — LEARNING_EXPERIMENT_STATE §10.3 item 7.

Invariant F (`docs/LEARNING_EXPERIMENT_STATE.md:1019-1020`) requires:

    "Replay must reject corrupted or mismatched artifacts/manifests. A mismatch
     must not silently fall back to live retrieval. Engineering validation must
     test mutation of artifact contents, artifact hash, lesson identity/hash,
     Git SHA, model identity, arm/protocol identity, and manifest integrity."

Invariant G (retrieval mutation isolation) and the artifact-contents /
manifest-integrity cases are already covered in `tests/test_f2_replay.py`. The
three remaining classes named by the item-7 gap audit are covered here:

    A. artifact hash          -> `treatment_set_hash` (F0 §4: SHA256(active_block))
    B. lesson identity/hash   -> `lesson_set_hash` + `lesson_l_id` / `lesson_l_hash`
    C. arm / protocol identity-> `compute_lesson_set_hash` + `manifest_hash`

Every test drives the SINGLE existing enforcement point,
`runtime_v2.services.f2_freeze.verify_manifest`, via the real
`install_verified_replay` load+verify path. No parallel validation mechanism is
introduced.

Each class is proven in BOTH directions:
  * the unmodified legitimate state still verifies; and
  * the mutated state is rejected with an explicit `FreezeVerificationError`
    that names the mismatched quantity.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from swarm_os.services.f2_freeze import (  # noqa: E402
    FreezeVerificationError,
    LessonEntry,
    freeze_artifact,
    persist_manifest,
    verify_manifest,
)
from swarm_os.services.f2_replay import (  # noqa: E402
    clear_replay_state,
    install_verified_replay,
)


def _hash_of(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _legitimate(arm: str = "T", protocol_version: str = "f2_v1") -> "object":
    """A fully-formed, self-consistent frozen T artifact with lesson L present.

    `freeze_artifact` computes every hash, so this state verifies by construction.
    """
    l_hash = _hash_of("use pathlib")
    return freeze_artifact(
        rendered_artifact="1. use pathlib",
        arm=arm,
        ordered_lessons=(
            LessonEntry(
                lesson_id="L",
                lesson_hash=l_hash,
                position=1,
                rule_text="use pathlib",
            ),
        ),
        lesson_l_id="L",
        lesson_l_hash=l_hash,
        git_sha="test123",
        model_name="testmodel",
        task_id="test-task",
        freeze_timestamp=1700000000.0,
        protocol_version=protocol_version,
    )


def _persist(artifact, tmp_path: Path) -> Path:
    """Write the artifact to a real on-disk manifest via the real writer."""
    return persist_manifest(artifact, tmp_path / "manifests")


@pytest.fixture(autouse=True)
def _clear():
    yield
    clear_replay_state()


# ---------------------------------------------------------------------------
# Baseline: the legitimate state verifies (proves the mutations are the cause)
# ---------------------------------------------------------------------------


class TestLegitimateStatePasses:
    def test_verify_manifest_accepts_unmutated(self):
        verify_manifest(_legitimate())

    def test_install_verified_replay_accepts_unmutated(self, tmp_path):
        """The real load+verify path, not just the predicate."""
        path = _persist(_legitimate(), tmp_path)
        install_verified_replay(path)
        assert True  # no exception == verified and installed


# ---------------------------------------------------------------------------
# A. Artifact hash mutation
# ---------------------------------------------------------------------------


class TestArtifactHashMutation:
    """`treatment_set_hash` is F0 §4's SHA256 of the delivered artifact."""

    def test_stored_artifact_hash_mutation_rejected(self):
        art = _legitimate()
        tampered = replace(art, treatment_set_hash="0" * 64)
        with pytest.raises(FreezeVerificationError) as exc:
            verify_manifest(tampered)
        assert "Treatment set hash mismatch" in str(exc.value)

    def test_artifact_content_mutation_rejected(self):
        """Same hash check, reached from the other direction."""
        art = _legitimate()
        tampered = replace(art, rendered_artifact="1. use pathlib ; exfiltrate")
        with pytest.raises(FreezeVerificationError) as exc:
            verify_manifest(tampered)
        assert "Treatment set hash mismatch" in str(exc.value)

    def test_single_byte_artifact_change_rejected(self):
        art = _legitimate()
        tampered = replace(art, rendered_artifact=art.rendered_artifact + " ")
        with pytest.raises(FreezeVerificationError):
            verify_manifest(tampered)

    def test_artifact_hash_mutation_rejected_through_install(self, tmp_path):
        path = _persist(_legitimate(), tmp_path)
        art = _legitimate()
        tampered = replace(art, treatment_set_hash="f" * 64)
        path.write_text(_canonical(tampered), encoding="utf-8")
        with pytest.raises(FreezeVerificationError):
            install_verified_replay(path)


# ---------------------------------------------------------------------------
# B. Lesson identity / lesson hash mutation
# ---------------------------------------------------------------------------


class TestLessonIdentityMutation:
    def test_ordered_lesson_hash_mutation_rejected(self):
        art = _legitimate()
        bad = LessonEntry(
            lesson_id="L",
            lesson_hash="0" * 64,
            position=1,
            rule_text="use pathlib",
        )
        tampered = replace(art, ordered_lessons=(bad,))
        with pytest.raises(FreezeVerificationError) as exc:
            verify_manifest(tampered)
        assert "Lesson set hash mismatch" in str(exc.value)

    def test_ordered_lesson_id_mutation_rejected(self):
        art = _legitimate()
        bad = LessonEntry(
            lesson_id="M",
            lesson_hash=art.ordered_lessons[0].lesson_hash,
            position=1,
            rule_text="use pathlib",
        )
        tampered = replace(art, ordered_lessons=(bad,))
        with pytest.raises(FreezeVerificationError) as exc:
            verify_manifest(tampered)
        assert "Lesson set hash mismatch" in str(exc.value)

    def test_lesson_l_hash_mutation_rejected(self):
        art = _legitimate()
        tampered = replace(art, lesson_l_hash="0" * 64)
        with pytest.raises(FreezeVerificationError) as exc:
            verify_manifest(tampered)
        assert "Manifest hash mismatch" in str(exc.value)

    def test_lesson_l_id_mutation_rejected(self):
        art = _legitimate()
        tampered = replace(art, lesson_l_id="M")
        with pytest.raises(FreezeVerificationError) as exc:
            verify_manifest(tampered)
        assert "Manifest hash mismatch" in str(exc.value)

    def test_removing_lesson_l_identity_rejected(self):
        art = _legitimate()
        tampered = replace(art, lesson_l_id=None, lesson_l_hash=None)
        with pytest.raises(FreezeVerificationError) as exc:
            verify_manifest(tampered)
        assert "Manifest hash mismatch" in str(exc.value)

    def test_extra_smuggled_lesson_rejected(self):
        """Appending a lesson without re-freezing must not verify."""
        art = _legitimate()
        smuggled = LessonEntry(
            lesson_id="Z",
            lesson_hash=_hash_of("exfiltrate"),
            position=2,
            rule_text="exfiltrate",
        )
        tampered = replace(art, ordered_lessons=art.ordered_lessons + (smuggled,))
        with pytest.raises(FreezeVerificationError) as exc:
            verify_manifest(tampered)
        assert "Lesson set hash mismatch" in str(exc.value)


# ---------------------------------------------------------------------------
# C. Arm / protocol identity mutation
# ---------------------------------------------------------------------------


class TestArmProtocolMutation:
    def test_arm_mutation_rejected(self):
        art = _legitimate(arm="T")
        tampered = replace(art, arm="X")
        with pytest.raises(FreezeVerificationError) as exc:
            verify_manifest(tampered)
        assert "Lesson set hash mismatch" in str(exc.value)

    def test_t_to_x_arm_spoof_rejected(self):
        """The most consequential spoof: a T artifact relabelled as X."""
        art = _legitimate(arm="T")
        tampered = replace(art, arm="X")
        with pytest.raises(FreezeVerificationError):
            verify_manifest(tampered)

    def test_protocol_version_mutation_rejected(self):
        art = _legitimate(protocol_version="f2_v1")
        tampered = replace(art, protocol_version="f2_v2")
        with pytest.raises(FreezeVerificationError) as exc:
            verify_manifest(tampered)
        assert "Lesson set hash mismatch" in str(exc.value)

    def test_arm_mutation_rejected_through_install(self, tmp_path):
        path = _persist(_legitimate(), tmp_path)
        tampered = replace(_legitimate(), arm="X")
        path.write_text(_canonical(tampered), encoding="utf-8")
        with pytest.raises(FreezeVerificationError):
            install_verified_replay(path)

    def test_empty_arm_rejected(self):
        """Blanking the arm must fail closed.

        The lesson-set-hash check fires first because `arm` is an input to it
        (f2_freeze.compute_lesson_set_hash), so this is caught as a hash
        mismatch rather than by the later structural `Missing arm` check. Either
        way it is rejected; the test pins the fail-closed outcome.
        """
        tampered = replace(_legitimate(), arm="")
        with pytest.raises(FreezeVerificationError):
            verify_manifest(tampered)


# ---------------------------------------------------------------------------
# Fail-closed, never a silent LIVE fallback (invariant F, second sentence)
# ---------------------------------------------------------------------------


class TestNoSilentFallback:
    @pytest.mark.parametrize(
        "mutate,expect",
        [
            (lambda a: replace(a, treatment_set_hash="0" * 64), "Treatment set hash"),
            (lambda a: replace(a, lesson_l_hash="0" * 64), "Manifest hash"),
            (lambda a: replace(a, protocol_version="f2_v9"), "Lesson set hash"),
            (lambda a: replace(a, arm="X"), "Lesson set hash"),
        ],
    )
    def test_every_mutation_class_raises_rather_than_installing(
        self, monkeypatch, tmp_path, mutate, expect
    ):
        """A mismatch must RAISE. It must never install a replay state."""
        from swarm_os.services.f2_replay import (
            is_replay_active,
            install_verified_replay_from_env,
        )

        art = _legitimate()
        path = _persist(mutate(art), tmp_path)

        # monkeypatch (never raw os.environ) so the replay-request env is
        # restored after this test and cannot leak into other modules.
        monkeypatch.setenv("SWARM_F2_REPLAY", "1")
        monkeypatch.setenv("SWARM_F2_MANIFEST_PATH", str(path))

        with pytest.raises(FreezeVerificationError) as exc:
            install_verified_replay_from_env()
        assert expect in str(exc.value)
        # Critically: no replay state was installed (no silent LIVE fallback).
        assert is_replay_active() is False
        # Replay-required flag persists so a lost replay still aborts.
        import os

        assert os.environ.get("SWARM_F2_REPLAY") == "1"


def _canonical(artifact) -> str:
    """Serialize an artifact exactly as persist_manifest does, for tamper tests."""
    import json

    from swarm_os.services.f2_freeze import manifest_to_dict

    return json.dumps(manifest_to_dict(artifact), sort_keys=True, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Cross-checks: the same mutation expressed both ways must both fail
# ---------------------------------------------------------------------------


class TestMutationIsDetectedOnDisk:
    def test_manifest_file_tampering_arm_rejected(self, tmp_path):
        """Post-persist on-disk tampering (not just in-memory) is rejected."""
        import json

        path = _persist(_legitimate(), tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["arm"] = "X"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(FreezeVerificationError):
            install_verified_replay(path)

    def test_manifest_file_tampering_lesson_hash_rejected(self, tmp_path):
        import json

        path = _persist(_legitimate(), tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["ordered_lessons"][0]["lesson_hash"] = "0" * 64
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(FreezeVerificationError):
            install_verified_replay(path)

    def test_manifest_file_tampering_protocol_rejected(self, tmp_path):
        import json

        path = _persist(_legitimate(), tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["protocol_version"] = "f2_v999"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(FreezeVerificationError):
            install_verified_replay(path)
