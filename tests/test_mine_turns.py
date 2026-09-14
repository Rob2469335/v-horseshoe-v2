"""Tests for the per-turn trajectory miner (qwen_train/mine_turns.py).

The miner turns the ATIF `step` records into the recovery / loop / tool-choice
signal. These tests pin the step-derived definitions so the run-level "recovery"
false positive (RUN2_OBSERVATIONS.md #1) cannot quietly return.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import mine_turns as mt  # noqa: E402


def _step(seq, tool, label, args=None, ok=True):
    return {
        "record_type": "step",
        "step_id": seq,
        "source": "agent",
        "model_name": "m",
        "tool_calls": [
            {
                "tool_call_id": f"r:{seq}",
                "function_name": tool,
                "arguments": args or {},
            }
        ],
        "observation": {
            "results": [
                {
                    "source_call_id": f"r:{seq}",
                    "content": "{}",
                    "extra": {"ok": ok, "label": label, "state": {"turn": seq}},
                }
            ]
        },
    }


def _write_run(tmp_path, steps, status="completed", task="do a thing"):
    p = tmp_path / "run-1.jsonl"
    lines = [json.dumps(s) for s in steps]
    lines.append(
        json.dumps(
            {
                "record_type": "summary",
                "run_id": "run-1",
                "agent_id": "coder",
                "status": status,
                "task": task,
                "last_content": "done",
            }
        )
    )
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_load_run_splits_steps_and_summary(tmp_path):
    p = _write_run(tmp_path, [_step(1, "filesystem", "NORMAL_SUCCESS")])
    run = mt.load_run(p)
    assert len(run["steps"]) == 1
    assert run["summary"]["status"] == "completed"


def test_analyze_detects_recovery_different_tool(tmp_path):
    p = _write_run(
        tmp_path,
        [
            _step(1, "filesystem", "FAILURE", ok=False),
            _step(2, "sandbox_repl", "NORMAL_SUCCESS"),
        ],
    )
    rec = mt.analyze(mt.load_run(p))
    assert rec["recovery"] is True
    assert rec["succeeded"] is True
    assert rec["calls_to_success"] == 2


def test_no_recovery_when_same_tool_retried(tmp_path):
    # A retry of the SAME tool is not an adapted approach (not recovery).
    p = _write_run(
        tmp_path,
        [
            _step(1, "filesystem", "FAILURE", ok=False),
            _step(2, "filesystem", "NORMAL_SUCCESS"),
        ],
    )
    assert mt.analyze(mt.load_run(p))["recovery"] is False


def test_recovery_after_ineligible(tmp_path):
    p = _write_run(
        tmp_path,
        [
            _step(1, "sandbox_repl", "INELIGIBLE", ok=False),
            _step(2, "filesystem", "NORMAL_SUCCESS"),
        ],
    )
    rec = mt.analyze(mt.load_run(p))
    assert rec["recovery"] is True
    assert rec["n_ineligible"] == 1


def test_analyze_detects_loop(tmp_path):
    p = _write_run(
        tmp_path,
        [
            _step(1, "filesystem", "NORMAL_SUCCESS", args={"operation": "list"}),
            _step(2, "filesystem", "NORMAL_SUCCESS", args={"operation": "list"}),
        ],
    )
    assert mt.analyze(mt.load_run(p))["loop"] is True


def test_no_loop_when_args_differ(tmp_path):
    p = _write_run(
        tmp_path,
        [
            _step(1, "filesystem", "NORMAL_SUCCESS", args={"path": "a.py"}),
            _step(2, "filesystem", "NORMAL_SUCCESS", args={"path": "b.py"}),
        ],
    )
    assert mt.analyze(mt.load_run(p))["loop"] is False
