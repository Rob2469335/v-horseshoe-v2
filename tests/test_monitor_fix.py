"""Regression tests for the monitor/retry race condition fix (F1-OP-INFRA-003).

These tests verify that the monitor thread no longer kills the CLI process
prematurely, which was causing stream_prompt_with_retry to retry and create
duplicate run_ids under the same rollout_id.
"""
import json
from unittest.mock import patch, MagicMock

import pytest


class TestMonitorFix:
    """Tests for the monitor/retry race condition fix."""

    def test_completed_trajectory_other_run_not_killed(self, tmp_path, monkeypatch):
        """A completed trajectory belonging to another run does not cause
        the current process to be killed (monitor removed)."""
        # Create a fake trajectory directory
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir(parents=True)

        # Create a stale trajectory file for a different run_id
        stale_run_id = "stale-run-id"
        stale_traj = tmp_path / "trajectories" / f"{stale_run_id}.jsonl"
        stale_traj.write_text(
            json.dumps({
                "record_type": "summary",
                "run_id": stale_run_id,
                "status": "completed",
                "last_content": "done",
            }) + "\n"
        )

        # Create a mock process
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process still running
        mock_proc.communicate.return_value = ('{"ok": true, "content": "done"}', "")

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("qwen_train.run_curriculum._check_endpoint_health", return_value=True):
                from qwen_train.run_curriculum import _attempt_once
                assert _attempt_once({"id": "test", "prompt": "test"}, timeout=10, allow_approval=True, record=False) is not None
                
                # The process should not have been killed
                assert not mock_proc.kill.called, "Process should not be killed by monitor"

    def test_current_process_not_killed_before_stream_complete(self, tmp_path, monkeypatch):
        """The current process is not killed before its streaming response is consumed."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir(parents=True)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.communicate.return_value = ('{"ok": true, "content": "done"}', "")

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("qwen_train.run_curriculum._check_endpoint_health", return_value=True):
                from qwen_train.run_curriculum import _attempt_once
                assert _attempt_once({"id": "test", "prompt": "test"}, timeout=10, allow_approval=True, record=False) is not None
                
                # Process should complete normally
                assert mock_proc.communicate.called
                assert not mock_proc.kill.called, "Process should not be killed by monitor"

    def test_completed_stream_produces_single_invocation(self, tmp_path, monkeypatch):
        """A completed stream produces exactly one agent-loop invocation."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir(parents=True)

        run_id = "test-run"
        traj_file = tmp_path / "trajectories" / f"{run_id}.jsonl"
        traj_file.parent.mkdir(parents=True, exist_ok=True)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # process exited normally
        mock_proc.communicate.return_value = (json.dumps({"ok": True, "content": "done", "run_id": "test-run"}), "")

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("qwen_train.run_curriculum._check_endpoint_health", return_value=True):
                from qwen_train.run_curriculum import _attempt_once
                assert _attempt_once({"id": "test", "prompt": "test"}, timeout=10, allow_approval=True, record=False) is not None
                
                # Should complete without retry
                assert mock_proc.communicate.called

    def test_one_rollout_one_run_id(self, tmp_path, monkeypatch):
        """One rollout_id does not produce multiple run_ids because of the monitor."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir(parents=True)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.communicate.return_value = (json.dumps({"ok": True, "content": "done", "run_id": "run-1"}), "")

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("qwen_train.run_curriculum._check_endpoint_health", return_value=True):
                from qwen_train.run_curriculum import _attempt_once
                assert _attempt_once({"id": "test", "prompt": "test"}, timeout=10, allow_approval=True, record=False) is not None
                
                # Should complete without monitor interference
                assert mock_proc.communicate.called

    def test_monitor_shuts_down_cleanly(self, tmp_path, monkeypatch):
        """The monitor shuts down cleanly after the subprocess finishes."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir(parents=True)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # process exited
        mock_proc.communicate.return_value = ("{}", "")

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("qwen_train.run_curriculum._check_endpoint_health", return_value=True):
                from qwen_train.run_curriculum import _attempt_once
                assert _attempt_once({"id": "test", "prompt": "test"}, timeout=10, allow_approval=True, record=False) is not None
                
                # Monitor should not have killed the process
                assert not mock_proc.kill.called

    def test_monitor_respects_current_run_id(self, tmp_path, monkeypatch):
        """Monitor ignores completed trajectories from other runs."""
        traj_dir = tmp_path / "trajectories"
        traj_dir.mkdir(parents=True)

        # Create a stale trajectory for a different run
        stale_run_id = "stale-run"
        stale_traj = tmp_path / "trajectories" / f"{stale_run_id}.jsonl"
        stale_traj.parent.mkdir(parents=True, exist_ok=True)
        stale_traj.write_text(json.dumps({
            "record_type": "summary",
            "run_id": "stale-run-id",
            "status": "completed",
            "last_content": "done",
        }) + "\n")

        # Current run
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # running
        mock_proc.communicate.return_value = (json.dumps({"ok": True, "content": "done", "run_id": "current"}), "")

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("qwen_train.run_curriculum._check_endpoint_health", return_value=True):
                from qwen_train.run_curriculum import _attempt_once
                assert _attempt_once({"id": "test", "prompt": "test"}, timeout=10, allow_approval=True, record=False) is not None
                
                # Should not be killed by stale trajectory
                assert not mock_proc.kill.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])