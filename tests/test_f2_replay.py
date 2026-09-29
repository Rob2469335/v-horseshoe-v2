"""F2 Replay Isolation — Phase 2 Tests.

Covers every verification target from the task specification:
A. Fresh-process replay (subprocess)
B. ContextVar installation
C. Live retrieval unavailable — frozen replay succeeds
D. Live state mutated — replay unchanged
E. Corrupted artifact — fails closed
F. Corrupted manifest — fails closed
G. Missing reference — fails closed
H. No live fallback
I. Parent/child distinction
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from swarm_os.services.f2_freeze import (
    FreezeVerificationError,
    freeze_artifact,
    persist_manifest,
)
from swarm_os.services.f2_replay import (
    ReplayMode,
    clear_replay_state,
    decode_manifest_reference,
    encode_manifest_reference,
    get_delivery_artifact,
    get_replay_state,
    install_replay_state,
    install_verified_replay,
    install_verified_replay_from_env,
    is_replay_active,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_frozen() -> freeze_artifact:
    """Convenience: create a frozen artifact for testing."""
    return freeze_artifact(
        rendered_artifact="1. Always use pathlib\n2. Verify before patching",
        arm="T",
        ordered_lessons=(),
        lesson_l_id=None,
        lesson_l_hash=None,
        git_sha="test123",
        model_name="testmodel",
        task_id="test-task",
        freeze_timestamp=1700000000.0,
    )


@pytest.fixture(autouse=True)
def _clear_replay():
    """Ensure replay state is cleared between tests."""
    yield
    clear_replay_state()


@pytest.fixture(scope="module", autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's autouse subprocess.Popen mock.

    Phase 2 tests spawn REAL child processes to prove cross-process
    replay isolation.  The conftest autouse mock returns (b"", b"") and
    would swallow all subprocess output.
    """
    yield


# ---------------------------------------------------------------------------
# A. Fresh-process replay (subprocess test)
# ---------------------------------------------------------------------------

# This script is executed as a subprocess child to prove cross-process replay.
_CHILD_SCRIPT = r'''
import sys, json
from pathlib import Path
from swarm_os.services.f2_freeze import load_manifest, verify_manifest
from swarm_os.services.f2_replay import (
    install_replay_state, get_delivery_artifact, get_replay_state,
    ReplayMode,
)

manifest_path = Path(sys.argv[1])
artifact = load_manifest(manifest_path)
verify_manifest(artifact)
state = install_replay_state(artifact, str(manifest_path))

result = {
    "mode": state.mode.value,
    "rendered_artifact": get_delivery_artifact(),
    "is_replay_active": state.mode == ReplayMode.FROZEN_REPLAY,
    "manifest_path": state.manifest_path,
    "arm": state.artifact.arm,
    "artifact_hash": state.artifact.artifact_hash,
}
print(json.dumps(result))
'''


def _run_child(tmp_path: Path, script: str, args: list[str] | None = None, extra_env: dict[str, str] | None = None) -> dict:
    """Run a child process script and return parsed JSON output."""
    script_path = tmp_path / "_child_script.py"
    script_path.write_text(script, encoding="utf-8")
    cmd = [sys.executable, "-u", str(script_path)] + (args or [])
    proc_env = dict(os.environ) if extra_env is None else {**os.environ, **extra_env}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parent.parent),
        env=proc_env,
    )
    stdout, stderr = proc.communicate(timeout=30)
    stdout_str = stdout.decode("utf-8", errors="replace")
    stderr_str = stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 0, f"Child failed (rc={proc.returncode}): {stderr_str}"
    decoded = stdout_str.strip()
    assert decoded, f"Child produced no stdout. stderr: {stderr_str}"
    return json.loads(decoded)


class TestFreshProcessReplay:
    def test_child_process_replays_frozen_artifact(self, tmp_path: Path):
        """A fresh subprocess can load a persisted manifest and replay it."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        data = _run_child(tmp_path, _CHILD_SCRIPT, args=[str(manifest_path)])
        assert data["mode"] == "frozen"
        assert data["rendered_artifact"] == artifact.rendered_artifact
        assert data["is_replay_active"] is True
        assert data["manifest_path"] == str(manifest_path)
        assert data["arm"] == "T"
        assert data["artifact_hash"] == artifact.artifact_hash


# ---------------------------------------------------------------------------
# B. ContextVar installation
# ---------------------------------------------------------------------------

class TestContextVarInstallation:
    def test_install_sets_replay_state(self):
        artifact = _make_frozen()
        state = install_replay_state(artifact, "/fake/path")
        assert get_replay_state() is state
        assert state.mode == ReplayMode.FROZEN_REPLAY
        assert state.artifact is artifact
        assert state.manifest_path == "/fake/path"

    def test_clear_resets_to_none(self):
        artifact = _make_frozen()
        install_replay_state(artifact, "/fake/path")
        clear_replay_state()
        assert get_replay_state() is None

    def test_is_replay_active_matches(self):
        artifact = _make_frozen()
        install_replay_state(artifact, "/fake/path")
        assert is_replay_active() is True
        clear_replay_state()
        assert is_replay_active() is False


# ---------------------------------------------------------------------------
# C. Live retrieval unavailable — frozen replay succeeds
# ---------------------------------------------------------------------------

class TestLiveRetrievalUnavailable:
    def test_frozen_replay_succeeds_without_qdrant(self, tmp_path: Path):
        """Replay works even when Qdrant / live retrieval is unavailable."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)
        install_verified_replay(manifest_path)

        # get_delivery_artifact returns the frozen text, no Qdrant needed
        delivered = get_delivery_artifact()
        assert delivered == artifact.rendered_artifact


# ---------------------------------------------------------------------------
# D. Live state mutated — replay unchanged
# ---------------------------------------------------------------------------

class TestLiveStateMutated:
    def test_replay_ignores_mutation_after_freeze(self, tmp_path: Path):
        """Freeze T → mutate live source → replay still returns frozen T."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        # Simulate "mutating the live retrieval source" by creating a different
        # artifact in the same directory (as if Qdrant content changed)
        mutated = freeze_artifact(
            rendered_artifact="completely different lesson content",
            arm="T",
            freeze_timestamp=2000000000.0,
            git_sha="mutated",
        )
        _ = persist_manifest(mutated, tmp_path)

        # Install replay from the ORIGINAL manifest (not the mutated one)
        install_verified_replay(manifest_path)

        # Replay must return the ORIGINAL artifact, not the mutated one
        delivered = get_delivery_artifact()
        assert delivered == artifact.rendered_artifact
        assert delivered != mutated.rendered_artifact


# ---------------------------------------------------------------------------
# E. Corrupted artifact — fails closed
# ---------------------------------------------------------------------------

class TestCorruptedArtifact:
    def test_corrupted_artifact_content_fails(self, tmp_path: Path):
        """Corrupting the artifact content inside the manifest fails verify."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        # Corrupt the rendered_artifact in the JSON
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        data["rendered_artifact"] = "CORRUPTED"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        with pytest.raises(FreezeVerificationError, match="Artifact hash mismatch"):
            install_verified_replay(manifest_path)

        # Must not have installed any replay state
        assert get_replay_state() is None


# ---------------------------------------------------------------------------
# F. Corrupted manifest — fails closed
# ---------------------------------------------------------------------------

class TestCorruptedManifest:
    def test_corrupted_manifest_hash_fails(self, tmp_path: Path):
        """Corrupting the manifest hash fails verification."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        # Corrupt the manifest_hash in the JSON
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        data["manifest_hash"] = "0" * 64
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        with pytest.raises(FreezeVerificationError, match="Manifest hash mismatch"):
            install_verified_replay(manifest_path)

        assert get_replay_state() is None

    def test_truncated_json_fails(self, tmp_path: Path):
        """A truncated JSON file fails at load time, not silently."""
        manifest_path = tmp_path / "truncated.json"
        manifest_path.write_text('{"schema_version": 1, "truncated": true}', encoding="utf-8")

        # load_manifest will succeed (valid JSON), but verify will fail
        with pytest.raises((FreezeVerificationError, KeyError, json.JSONDecodeError)):
            install_verified_replay(manifest_path)

        assert get_replay_state() is None


# ---------------------------------------------------------------------------
# G. Missing reference — fails closed
# ---------------------------------------------------------------------------

class TestMissingReference:
    def test_missing_manifest_file_fails(self, tmp_path: Path):
        """A missing manifest path raises FileNotFoundError."""
        missing = tmp_path / "nonexistent_f2_freeze.json"

        with pytest.raises(FileNotFoundError):
            install_verified_replay(missing)

        assert get_replay_state() is None

    def test_decode_manifest_reference_empty(self):
        """No env var → returns None (normal LIVE mode)."""
        os.environ.pop("SWARM_F2_MANIFEST_PATH", None)
        assert decode_manifest_reference() is None

    def test_decode_manifest_reference_preserves_env(self):
        """After decoding, the env var is preserved (clearing happens later)."""
        os.environ["SWARM_F2_MANIFEST_PATH"] = "/some/path.json"
        result = decode_manifest_reference()
        assert result == Path("/some/path.json")
        assert os.environ.get("SWARM_F2_MANIFEST_PATH") == "/some/path.json"
        os.environ.pop("SWARM_F2_MANIFEST_PATH", None)


# ---------------------------------------------------------------------------
# H. No live fallback
# ---------------------------------------------------------------------------

class TestNoLiveFallback:
    def test_verify_failure_never_returns_default(self, tmp_path: Path):
        """Verification failure raises, never returns a default artifact."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        # Corrupt the manifest hash
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        data["manifest_hash"] = "bad"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        raised = False
        try:
            install_verified_replay(manifest_path)
        except FreezeVerificationError:
            raised = True
        except Exception:
            pytest.fail("Should raise FreezeVerificationError, not other exception")

        assert raised
        assert get_replay_state() is None
        assert is_replay_active() is False
        assert get_delivery_artifact() == ""  # no default artifact

    def test_delivery_artifact_empty_when_no_replay(self):
        """Without replay installed, get_delivery_artifact returns ''."""
        clear_replay_state()
        assert get_delivery_artifact() == ""
        assert is_replay_active() is False

    def test_install_verified_replay_from_env_none_when_unset(self):
        """No env var → returns None (LIVE mode), no error."""
        os.environ.pop("SWARM_F2_MANIFEST_PATH", None)
        result = install_verified_replay_from_env()
        assert result is None
        assert is_replay_active() is False

    def test_install_verified_replay_from_env_fails_on_corrupt(self, tmp_path: Path):
        """Env var set but manifest corrupt → FreezeVerificationError, no fallback."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        # Corrupt
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        data["artifact_hash"] = "bad"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        os.environ["SWARM_F2_REPLAY"] = "1"
        os.environ["SWARM_F2_MANIFEST_PATH"] = str(manifest_path)
        try:
            with pytest.raises(FreezeVerificationError):
                install_verified_replay_from_env()
        finally:
            os.environ.pop("SWARM_F2_REPLAY", None)
            os.environ.pop("SWARM_F2_MANIFEST_PATH", None)

        assert get_replay_state() is None
        assert is_replay_active() is False
        assert get_delivery_artifact() == ""


# ---------------------------------------------------------------------------
# I. Parent/child distinction
# ---------------------------------------------------------------------------

class TestParentChildDistinction:
    def test_parent_contextvar_not_inherited_by_child(self, tmp_path: Path):
        """Parent ContextVar state does not leak into a fresh child process."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        install_verified_replay(manifest_path)
        assert is_replay_active() is True

        child_script = r"""
import sys, json
from swarm_os.services.f2_replay import is_replay_active, get_delivery_artifact
print(json.dumps({
    "is_active": is_replay_active(),
    "artifact": get_delivery_artifact(),
}))
"""
        data = _run_child(tmp_path, child_script)
        assert data["is_active"] is False
        assert data["artifact"] == ""

    def test_encode_decode_manifest_reference(self, tmp_path: Path):
        """encode/decode round-trip preserves the manifest path and sets replay flag."""
        path = tmp_path / "test_freeze.json"
        env = encode_manifest_reference(path)
        assert env["SWARM_F2_REPLAY"] == "1"
        assert env["SWARM_F2_MANIFEST_PATH"] == str(path)

        os.environ.update(env)
        decoded = decode_manifest_reference()
        assert decoded == path
        # Env vars are NOT cleared by decode — cleared only after successful install
        assert os.environ.get("SWARM_F2_MANIFEST_PATH") == str(path)
        assert os.environ.get("SWARM_F2_REPLAY") == "1"
        os.environ.pop("SWARM_F2_MANIFEST_PATH", None)
        os.environ.pop("SWARM_F2_REPLAY", None)

    def test_verified_replay_via_env(self, tmp_path: Path):
        """End-to-end: persist → env → child loads+verifies+installs."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        child_script = r"""
import sys, json
from swarm_os.services.f2_replay import install_verified_replay_from_env, get_delivery_artifact, is_replay_active
state = install_verified_replay_from_env()
print(json.dumps({
    "installed": state is not None,
    "is_active": is_replay_active(),
    "artifact": get_delivery_artifact(),
    "arm": state.artifact.arm if state else None,
}))
"""
        data = _run_child(tmp_path, child_script, extra_env={
            "SWARM_F2_REPLAY": "1",
            "SWARM_F2_MANIFEST_PATH": str(manifest_path),
        })
        assert data["installed"] is True
        assert data["is_active"] is True
        assert data["artifact"] == artifact.rendered_artifact
        assert data["arm"] == "T"


# ---------------------------------------------------------------------------
# Explicit replay-request fail-closed tests
# ---------------------------------------------------------------------------

class TestExplicitReplayRequest:
    """Tests proving that FROZEN_REPLAY + missing/broken evidence fails closed."""

    def test_replay_requested_missing_manifest_fails(self):
        """REPLAY=1 but no manifest path → FreezeVerificationError, not LIVE."""
        os.environ["SWARM_F2_REPLAY"] = "1"
        os.environ.pop("SWARM_F2_MANIFEST_PATH", None)
        try:
            with pytest.raises(FreezeVerificationError, match="manifest reference missing"):
                install_verified_replay_from_env()
        finally:
            os.environ.pop("SWARM_F2_REPLAY", None)

        # State must NOT be LIVE — replay failure must not silently become LIVE
        assert get_replay_state() is None
        assert is_replay_active() is False
        assert get_delivery_artifact() == ""

    def test_replay_requested_nonexistent_manifest_fails(self):
        """REPLAY=1 but manifest file doesn't exist → FileNotFoundError."""
        os.environ["SWARM_F2_REPLAY"] = "1"
        os.environ["SWARM_F2_MANIFEST_PATH"] = "/nonexistent/path.json"
        try:
            with pytest.raises(FileNotFoundError):
                install_verified_replay_from_env()
        finally:
            os.environ.pop("SWARM_F2_REPLAY", None)
            os.environ.pop("SWARM_F2_MANIFEST_PATH", None)

        assert get_replay_state() is None

    def test_replay_requested_corrupt_manifest_fails(self, tmp_path: Path):
        """REPLAY=1 but manifest is corrupt → FreezeVerificationError."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        data["manifest_hash"] = "0" * 64
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        os.environ["SWARM_F2_REPLAY"] = "1"
        os.environ["SWARM_F2_MANIFEST_PATH"] = str(manifest_path)
        try:
            with pytest.raises(FreezeVerificationError, match="Manifest hash mismatch"):
                install_verified_replay_from_env()
        finally:
            os.environ.pop("SWARM_F2_REPLAY", None)
            os.environ.pop("SWARM_F2_MANIFEST_PATH", None)

        assert get_replay_state() is None
        assert is_replay_active() is False

    def test_replay_requested_corrupt_artifact_fails(self, tmp_path: Path):
        """REPLAY=1 but artifact content is corrupt → FreezeVerificationError."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        data["rendered_artifact"] = "CORRUPTED"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        os.environ["SWARM_F2_REPLAY"] = "1"
        os.environ["SWARM_F2_MANIFEST_PATH"] = str(manifest_path)
        try:
            with pytest.raises(FreezeVerificationError, match="Artifact hash mismatch"):
                install_verified_replay_from_env()
        finally:
            os.environ.pop("SWARM_F2_REPLAY", None)
            os.environ.pop("SWARM_F2_MANIFEST_PATH", None)

        assert get_replay_state() is None

    def test_verification_failure_does_not_become_live(self, tmp_path: Path):
        """Critical invariant: replay failure never silently turns into LIVE."""
        artifact = _make_frozen()
        manifest_path = persist_manifest(artifact, tmp_path)

        # Verify state before the attempt
        assert get_replay_state() is None
        assert is_replay_active() is False
        assert get_delivery_artifact() == ""

        # Corrupt the manifest
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        data["artifact_hash"] = "bad"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # Request replay
        os.environ["SWARM_F2_REPLAY"] = "1"
        os.environ["SWARM_F2_MANIFEST_PATH"] = str(manifest_path)
        try:
            with pytest.raises(FreezeVerificationError):
                install_verified_replay_from_env()
        finally:
            os.environ.pop("SWARM_F2_REPLAY", None)
            os.environ.pop("SWARM_F2_MANIFEST_PATH", None)

        # After failure, state must still be None (no replay installed)
        # and the process must NOT have entered LIVE mode silently
        assert get_replay_state() is None
        assert is_replay_active() is False
        assert get_delivery_artifact() == ""

    def test_explicit_live_mode_works(self):
        """When REPLAY is not set, install_verified_replay_from_env returns None (LIVE)."""
        os.environ.pop("SWARM_F2_REPLAY", None)
        os.environ.pop("SWARM_F2_MANIFEST_PATH", None)

        result = install_verified_replay_from_env()
        assert result is None
        assert get_replay_state() is None
        assert is_replay_active() is False
        assert get_delivery_artifact() == ""
