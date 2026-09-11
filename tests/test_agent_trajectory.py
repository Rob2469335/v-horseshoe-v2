"""Per-run trajectory writer: status derivation (the retrain-selectable
outcome field of the KARL-style trajectory ledger)."""

import json

from runtime_v2.api import agent_service_v2 as agent


def _write(tmp_path, chunk):
    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    svc._TRAJ_DIR = tmp_path
    svc._write_run_trajectory(
        run_id="traj-test",
        agent_id="code_analyzer",
        parent_id="",
        delegated_by="",
        prompt="analyze my codebase for bugs and upgrades",
        genome_id="",
        last_chunk=chunk,
    )
    line = next(
        ln for ln in (tmp_path / "traj-test.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()
    )
    return json.loads(line)


def test_trajectory_status_max_turns(tmp_path):
    # 2026-09-10: max-turns finals yielded content="[System: max turns reached]"
    # with no action/handler_status, so status stayed "unknown" — the ledger was
    # not selectable by outcome. Revert-proof: fails on pre-fix status derivation.
    rec = _write(tmp_path, {"type": "final", "content": "[System: max turns reached]"})
    assert rec["status"] == "max_turns"


def test_trajectory_status_completed_plain_final(tmp_path):
    rec = _write(tmp_path, {"action": "final", "content": "real findings report ..."})
    assert rec["status"] == "completed"


def test_trajectory_status_aborted_when_no_content(tmp_path):
    rec = _write(tmp_path, {"type": "final", "content": ""})
    assert rec["status"] == "aborted"


def test_trajectory_status_honors_handler_status(tmp_path):
    rec = _write(tmp_path, {"handler_status": "DONE", "content": "x"})
    assert rec["status"] == "done"