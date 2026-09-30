"""F2 rollout-identity binding at the P2 delivery seam.

Proves the producing half of the P1↔P2 identity contract: the value P1
propagates into the fresh P2 process (``SWARM_F2_ROLLOUT_ID``) is what P2 stamps
into its delivery-evidence record. P1 selects on this field, so if P2 stamped
nothing, or something else, P1 could never bind the evidence.

Unit-level only: no service is started, no F2 arm is run, no model is called.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qwen_train.f2_arm_primitives import lesson_hash_of  # noqa: E402
from runtime_v2.services.f2_freeze import (  # noqa: E402
    LessonEntry,
    freeze_artifact,
)
from runtime_v2.api.agent_service_v2 import (  # noqa: E402
    _current_f2_rollout_id,
    _f2_delivery_evidence_chunk,
)

F2_ROLLOUT_ENV = "SWARM_F2_ROLLOUT_ID"


class TestP2ReadsPropagatedRolloutId:
    def test_reads_value_p1_propagated(self, monkeypatch):
        """P2 reports exactly the rollout identity P1 put in its environment."""
        monkeypatch.setenv(F2_ROLLOUT_ENV, "rollout-from-p1-abc123")
        assert _current_f2_rollout_id() == "rollout-from-p1-abc123"

    def test_absent_env_is_empty(self, monkeypatch):
        """No propagated identity → empty, never a synthesized value."""
        monkeypatch.delenv(F2_ROLLOUT_ENV, raising=False)
        assert _current_f2_rollout_id() == ""

    def test_blank_env_is_empty(self, monkeypatch):
        """Whitespace-only identity is not a usable binding."""
        monkeypatch.setenv(F2_ROLLOUT_ENV, "   ")
        assert _current_f2_rollout_id() == ""

    def test_surrounding_whitespace_trimmed(self, monkeypatch):
        """Trailing newline from a shell export does not corrupt the identity."""
        monkeypatch.setenv(F2_ROLLOUT_ENV, "  rollout-xyz  \n")
        assert _current_f2_rollout_id() == "rollout-xyz"


class TestBindingIsDistinctFromRequestHeaderIdentity:
    def test_env_identity_is_not_the_request_contextvar(self, monkeypatch):
        """The F2 binding is the env value, not ROLLOUT_ID_CTX.

        F2 arms do not arrive over the credential-gated header path, so the
        request ContextVar stays unset while the env identity is present. This
        is why the delivery seam must read the env value to bind the record.
        """
        from runtime_v2.services.stream_runner import ROLLOUT_ID_CTX

        monkeypatch.setenv(F2_ROLLOUT_ENV, "rollout-from-p1")
        token = ROLLOUT_ID_CTX.set(None)
        try:
            assert ROLLOUT_ID_CTX.get() is None
            assert _current_f2_rollout_id() == "rollout-from-p1"
        finally:
            ROLLOUT_ID_CTX.reset(token)


class TestSeamEmitsBindingAndPersists:
    """The changed production code: seam chunk → trajectory record → P1 reader.

    These build the chunk exactly as ``agent_service_v2`` yields it (real
    ``record_f2_delivery_evidence`` output + the seam's identity fields), persist
    it with the real persistence shape, then read it back with the real reader.
    A regression that drops ``rollout_id`` from the seam fails here.
    """

    P1_ROLLOUT_ID = "rollout-p1-6f1c0a9b2d3e4f50"
    P2_RUN_ID = "7e6d5c4b-3a29-4180-9f8e7d6c5b4a"

    def _seam_chunk(self, monkeypatch, rollout_id: str, run_id: str) -> dict:
        """Call the REAL delivery-seam production function."""
        from runtime_v2.services.f2_replay import (
            clear_replay_state,
            install_replay_state,
        )

        lesson = LessonEntry(
            lesson_id="L",
            lesson_hash=lesson_hash_of("use pathlib"),
            position=1,
            rule_text="use pathlib",
        )
        artifact = freeze_artifact(
            rendered_artifact="use pathlib",
            arm="T",
            ordered_lessons=(lesson,),
            lesson_l_id="L",
            lesson_l_hash=lesson.lesson_hash,
            task_id="task-1",
            git_sha="deadbeef",
            model_name="test-model",
            freeze_timestamp=1_700_000_000.0,
        )
        install_replay_state(artifact, str(_REPO_ROOT / "manifest.json"))
        try:
            monkeypatch.setenv(F2_ROLLOUT_ENV, rollout_id)
            return _f2_delivery_evidence_chunk(
                run_id, "use pathlib", "SYS", "f2probe"
            )
        finally:
            clear_replay_state()

    def test_seam_chunk_carries_propagated_rollout(self, monkeypatch):
        """The seam stamps P1's rollout identity onto the evidence chunk."""
        chunk = self._seam_chunk(monkeypatch, self.P1_ROLLOUT_ID, self.P2_RUN_ID)
        assert chunk["rollout_id"] == self.P1_ROLLOUT_ID
        assert chunk["run_id"] == self.P2_RUN_ID
        assert chunk["rollout_id"] != chunk["run_id"]

    def test_round_trip_seam_to_p1_reader(self, monkeypatch, tmp_path):
        """Chunk → real persistence shape → real P1 reader returns the record."""
        from qwen_train.f2_arm_worker import _read_p2_delivery_evidence

        chunk = self._seam_chunk(monkeypatch, self.P1_ROLLOUT_ID, self.P2_RUN_ID)

        traj = tmp_path / "data" / "trajectories"
        traj.mkdir(parents=True)
        # The persistence shape written by step_agent_stream.
        with open(traj / f"{self.P2_RUN_ID}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"record_type": "delivery_evidence", **chunk}) + "\n"
            )

        result = _read_p2_delivery_evidence(tmp_path, rollout_id=self.P1_ROLLOUT_ID)
        assert result is not None, "P1 could not bind its own rollout's evidence"
        assert result["rollout_id"] == self.P1_ROLLOUT_ID
        # P2's authoritative values survive intact.
        assert result["final_prompt_hash"]
        assert result["serving_pid"] == os.getpid()
