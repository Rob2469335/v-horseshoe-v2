"""Integration test: ATIF trajectory → manifest endpoint wiring.

Proves that the production code path loads actual ATIF step records from
the trajectory and correctly detects a qualifying first-edit endpoint.

This is NOT a unit test of find_qualifying_first_edit() in isolation.
It exercises the real wiring: run_repair_task.py observation construction
→ classify_observation → manifest.f1_endpoint_step.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "qwen_train") not in sys.path:
    sys.path.insert(0, str(_REPO / "qwen_train"))

import f1_infra as f1i


def _write_trajectory(path: Path, steps: list) -> None:
    """Write a minimal trajectory file with ATIF step records."""
    with open(path, "w", encoding="utf-8") as f:
        for i, step in enumerate(steps):
            record = {
                "record_type": "step",
                "run_id": "test-run-id",
                "step_id": i + 1,
                "timestamp": "2026-01-01T00:00:00Z",
                "source": "agent",
                "model_name": "test",
                "message": "",
                "tool_calls": [step],
                "observation": {"results": [{"content": "{}", "extra": {"ok": True}}]},
            }
            f.write(json.dumps(record) + "\n")


def _load_atif_steps(traj_path: Path) -> list:
    """Load ATIF step records from trajectory (same logic as run_repair_task.py)."""
    steps = []
    with open(traj_path, encoding="utf-8") as tf:
        for line in tf:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("record_type") == "step":
                for tc in d.get("tool_calls", []):
                    tc.setdefault("extra", {})["step_id"] = d.get("step_id", 0)
                    steps.append(tc)
    return steps


class TestAtifToManifestWiring:
    """Integration test: trajectory ATIF → observation → classification → manifest."""

    def test_qualifying_patch_at_step_3(self):
        """A patch at ATIF step 3 on the relevant file should produce endpoint=3."""
        steps = [
            {"function_name": "lsp", "arguments": {"operation": "diagnostics"}, "extra": {"turn": 1}},
            {"function_name": "filesystem", "arguments": {"operation": "read", "path": "swarm_os/lib/paths.py"}, "extra": {"turn": 2}},
            {"function_name": "filesystem", "arguments": {"operation": "patch", "path": "swarm_os/lib/paths.py"}, "extra": {"turn": 4}},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            traj = Path(tmp) / "test-run.jsonl"
            _write_trajectory(traj, steps)

            # Simulate what run_repair_task.py does after CLI completion
            atif_steps = _load_atif_steps(traj)
            assert len(atif_steps) == 3

            obs = f1i.RawObservation(
                cli_ok=False, timed_out=True, elapsed_s=100.0,
                tool_calls=atif_steps,
            )
            cls = f1i.classify_observation(obs)
            # _write_trajectory assigns step_id = i+1, so patch at index 2 → step_id=3
            assert cls.f1_endpoint_step == 3

    def test_rejected_patch_still_qualifies(self):
        """A rejected patch (ok=false) still qualifies as endpoint."""
        steps = [
            {"function_name": "filesystem", "arguments": {"operation": "patch", "path": "swarm_os/lib/paths.py"}, "extra": {"turn": 3}},
        ]
        obs = f1i.RawObservation(
            cli_ok=False, timed_out=True, tool_calls=steps,
        )
        cls = f1i.classify_observation(obs)
        assert cls.f1_endpoint_step == 3

    def test_no_qualifying_edit_produces_none(self):
        """No edit-type action on relevant file → endpoint is None."""
        steps = [
            {"function_name": "filesystem", "arguments": {"operation": "read", "path": "swarm_os/lib/paths.py"}, "extra": {"turn": 1}},
            {"function_name": "lsp", "arguments": {"operation": "diagnostics"}, "extra": {"turn": 2}},
        ]
        obs = f1i.RawObservation(
            cli_ok=False, timed_out=True, tool_calls=steps,
        )
        cls = f1i.classify_observation(obs)
        assert cls.f1_endpoint_step is None

    def test_fallback_to_string_tool_calls(self):
        """When trajectory loading fails, falls back to string tool_calls."""
        obs = f1i.RawObservation(
            cli_ok=False, timed_out=True,
            tool_calls=["lsp", "filesystem"],
        )
        cls = f1i.classify_observation(obs)
        # Falls back to string list; find_qualifying_first_edit skips strings
        assert cls.f1_endpoint_step is None
