"""End-to-end checkpoint resume test.

Proves that runtime state can:
1. Begin an agent run
2. Create/persist a checkpoint
3. Lose in-memory state (simulate restart)
4. Load the checkpoint
5. Restore expected messages/state/turn/loop-guards
6. Continue from the restored state
"""
from __future__ import annotations


import pytest

from runtime_v2.services.checkpointing import (
    checkpoint_id,
    write_checkpoint,
    load_checkpoint,
    delete_checkpoint,
)
from runtime_v2.services import checkpointing as ck
from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState


@pytest.fixture(autouse=True)
def _isolate_checkpoint_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(ck, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    return tmp_path


class TestCheckpointIdStability:
    def test_same_goal_same_id(self):
        assert checkpoint_id("coder", "fix bug") == checkpoint_id("coder", "fix bug")

    def test_normalized_whitespace(self):
        assert checkpoint_id("coder", "  fix   bug  ") == checkpoint_id(
            "coder", "fix bug"
        )

    def test_different_goal_different_id(self):
        assert checkpoint_id("coder", "fix bug") != checkpoint_id(
            "coder", "fix other bug"
        )

    def test_different_agent_different_id(self):
        assert checkpoint_id("coder", "fix bug") != checkpoint_id(
            "debugger", "fix bug"
        )


class TestCheckpointWriteLoad:
    def test_write_and_load_roundtrip(self):
        cid = checkpoint_id("coder", "test goal")
        payload = {
            "checkpoint_id": cid,
            "turn": 3,
            "messages": [
                {"role": "system", "content": "You are a coder."},
                {"role": "user", "content": "Fix the bug."},
                {"role": "assistant", "content": "Reading files..."},
            ],
            "state": {"did_code_change": True, "read_paths": ["twine/package.py"]},
            "loop_guards": {"consecutive_errors": 0, "healing_attempts": 0},
        }
        write_checkpoint(cid, payload)
        loaded = load_checkpoint(cid)
        assert loaded is not None
        assert loaded["turn"] == 3
        assert loaded["state"]["did_code_change"] is True
        assert "twine/package.py" in loaded["state"]["read_paths"]
        assert len(loaded["messages"]) == 3

    def test_load_returns_none_when_missing(self):
        assert load_checkpoint("nonexistent") is None

    def test_delete_removes_checkpoint(self):
        cid = checkpoint_id("coder", "delete me")
        write_checkpoint(cid, {"checkpoint_id": cid, "turn": 1, "messages": [], "state": {}})
        assert load_checkpoint(cid) is not None
        delete_checkpoint(cid)
        assert load_checkpoint(cid) is None


class TestCheckpointResumeRoundTrip:
    def test_callstate_roundtrip_via_checkpoint(self):
        """_CallState fields survive the write/load cycle."""
        state = _CallState()
        state.did_code_change = True
        state.read_paths = ["a.py", "b.py"]
        state.test_pass_result = 1.0
        state._tests_ran = True
        state._tool_successes = 5
        state._tool_attempts = 7
        state._turn = 4
        state._web_final_rejected = True
        state.did_web_search = True
        state.did_web_fetch = False
        state.genome_id = "genome_123"
        state.run_id = "run_abc"

        # Simulate what the agent loop saves
        _svc = AgentServiceV2.__new__(AgentServiceV2)
        saved = _svc._state_to_dict(state)

        cid = checkpoint_id("coder", "test resume")
        write_checkpoint(cid, {
            "checkpoint_id": cid,
            "turn": state._turn,
            "messages": [{"role": "user", "content": "Fix the bug."}],
            "state": saved,
            "loop_guards": {
                "consecutive_errors": 0,
                "healing_attempts": 0,
                "premature_finals": 0,
            },
        })

        # Load and restore
        loaded = load_checkpoint(cid)
        assert loaded is not None
        restored = _svc._state_from_dict(_CallState(), loaded["state"])
        assert restored.did_code_change is True
        assert restored.read_paths == {"a.py", "b.py"}
        assert restored.test_pass_result == 1.0
        assert restored._tests_ran is True
        assert restored._tool_successes == 5
        assert restored._tool_attempts == 7
        assert restored._turn == 4
        assert restored.did_web_search is True
        assert restored.did_web_fetch is False
        assert restored.genome_id == "genome_123"
        assert restored.run_id == "run_abc"

    def test_checkpoint_turn_is_restored(self):
        """The turn number is preserved across checkpoint roundtrip."""
        cid = checkpoint_id("coder", "turn test")
        write_checkpoint(cid, {
            "checkpoint_id": cid,
            "turn": 7,
            "messages": [],
            "state": {},
            "loop_guards": {},
        })
        loaded = load_checkpoint(cid)
        assert loaded["turn"] == 7

    def test_checkpoint_delegation_chain_is_restored(self):
        """The delegation chain is preserved."""
        cid = checkpoint_id("coder", "chain test")
        chain = ["coordinator", "coder", "debugger"]
        write_checkpoint(cid, {
            "checkpoint_id": cid,
            "turn": 2,
            "messages": [],
            "state": {},
            "delegation_chain": chain,
            "loop_guards": {},
        })
        loaded = load_checkpoint(cid)
        assert loaded["delegation_chain"] == chain
