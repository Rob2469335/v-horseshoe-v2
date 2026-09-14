"""Per-turn ATIF-shaped trajectory step capture.

The run summary line (data/trajectories/<run_id>.jsonl, last line) carries the
outcome; these tests cover the per-turn `step` records appended before it —
the substrate the recovery/critical-step miner needs (docs/RUN2_OBSERVATIONS.md
priority #1). Revert-proof: every assertion here fails on pre-capture source
(no step lines exist, so the file has only the summary).
"""

import json

import pytest

from runtime_v2.api import agent_service_v2 as agent


def _svc(tmp_path):
    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    svc._TRAJ_DIR = tmp_path
    return svc


def _state(tool_result=None, **kw):
    st = agent._CallState()
    st.run_id = "run-abc"
    st._turn = 2
    st.tool_result = tool_result if tool_result is not None else {"ok": True}
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def _steps(path):
    lines = [
        ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    return [json.loads(ln) for ln in lines]


def test_step_has_atif_shape(tmp_path):
    svc = _svc(tmp_path)
    st = _state(
        {"ok": True, "result": "sum=42"},
        did_code_change=True,
        _tool_successes=1,
        _tool_attempts=1,
    )
    svc._write_run_step(
        run_id="run-abc",
        agent_id="coder",
        model="deepseek-v4-flash",
        decision={"action": "sandbox_repl", "code": "print(42)"},
        state=st,
    )
    rec = _steps(tmp_path / "run-abc.jsonl")[0]

    assert rec["record_type"] == "step"
    assert rec["schema_version"] == "ATIF-v1.4"
    assert rec["source"] == "agent"
    assert rec["model_name"] == "deepseek-v4-flash"
    assert rec["step_id"] == 1

    tc = rec["tool_calls"][0]
    assert tc["function_name"] == "sandbox_repl"
    assert tc["arguments"] == {"code": "print(42)"}  # action stripped
    assert tc["extra"]["turn"] == 2

    res = rec["observation"]["results"][0]
    assert res["source_call_id"] == tc["tool_call_id"]
    assert res["extra"]["ok"] is True
    assert res["extra"]["label"] == "NORMAL_SUCCESS"
    assert res["extra"]["state"]["tool_successes"] == 1
    assert res["extra"]["state_hash"], "state_hash must be present"


def test_step_ids_increment(tmp_path):
    svc = _svc(tmp_path)
    st = _state()
    for _ in range(2):
        svc._write_run_step(
            run_id="run-abc", agent_id="coder", model="m", decision={"action": "t"}, state=st
        )
    ids = [r["step_id"] for r in _steps(tmp_path / "run-abc.jsonl")]
    assert ids == [1, 2]


def test_state_hash_is_deterministic_and_state_sensitive(tmp_path):
    a = agent.AgentServiceV2._state_snapshot(_state(_tool_successes=1))
    b = agent.AgentServiceV2._state_snapshot(_state(_tool_successes=1))
    c = agent.AgentServiceV2._state_snapshot(_state(_tool_successes=2))
    assert agent.AgentServiceV2._state_hash(a) == agent.AgentServiceV2._state_hash(b)
    assert agent.AgentServiceV2._state_hash(a) != agent.AgentServiceV2._state_hash(c)


def test_label_failure_for_plain_error(tmp_path):
    svc = _svc(tmp_path)
    st = _state({"ok": False, "error": "command not found"})
    svc._write_run_step(
        run_id="run-abc", agent_id="coder", model="m", decision={"action": "x"}, state=st
    )
    res = _steps(tmp_path / "run-abc.jsonl")[0]["observation"]["results"][0]
    assert res["extra"]["label"] == "FAILURE"


def test_label_ineligible_on_confirmation_required(tmp_path):
    svc = _svc(tmp_path)
    st = _state({"ok": False, "status": "confirmation_required"})
    svc._write_run_step(
        run_id="run-abc", agent_id="coder", model="m", decision={"action": "x"}, state=st
    )
    res = _steps(tmp_path / "run-abc.jsonl")[0]["observation"]["results"][0]
    assert res["extra"]["label"] == "INELIGIBLE"


def test_label_ineligible_on_deny(tmp_path):
    svc = _svc(tmp_path)
    st = _state({"ok": False, "error": "Authorization DENIED by user."})
    svc._write_run_step(
        run_id="run-abc", agent_id="coder", model="m", decision={"action": "x"}, state=st
    )
    res = _steps(tmp_path / "run-abc.jsonl")[0]["observation"]["results"][0]
    assert res["extra"]["label"] == "INELIGIBLE"


def test_label_environment_failure_on_timeout(tmp_path):
    svc = _svc(tmp_path)
    st = _state({"ok": False, "error": "Timeout: tool timed out"})
    svc._write_run_step(
        run_id="run-abc", agent_id="coder", model="m", decision={"action": "x"}, state=st
    )
    res = _steps(tmp_path / "run-abc.jsonl")[0]["observation"]["results"][0]
    assert res["extra"]["label"] == "ENVIRONMENT_FAILURE"


def test_content_truncated_to_cap(tmp_path):
    svc = _svc(tmp_path)
    st = _state({"ok": True, "result": "x" * 20000})
    svc._write_run_step(
        run_id="run-abc", agent_id="coder", model="m", decision={"action": "x"}, state=st
    )
    res = _steps(tmp_path / "run-abc.jsonl")[0]["observation"]["results"][0]
    assert len(res["content"]) <= agent.AgentServiceV2._TRAJ_CONTENT_CAP


def test_summary_stays_last_after_steps(tmp_path):
    # score_trajectories.py reads lines[-1] as the run summary; per-turn steps
    # must therefore precede it.
    svc = _svc(tmp_path)
    st = _state()
    svc._write_run_step(
        run_id="run-abc", agent_id="coder", model="m", decision={"action": "x"}, state=st
    )
    svc._write_run_trajectory(
        run_id="run-abc",
        agent_id="coder",
        parent_id="",
        delegated_by="",
        prompt="do a thing",
        genome_id="",
        last_chunk={"action": "final", "content": "done"},
    )
    recs = _steps(tmp_path / "run-abc.jsonl")
    assert recs[-1]["record_type"] == "summary"
    assert recs[-1]["status"] == "completed"
    assert recs[0]["record_type"] == "step"


def test_empty_run_id_writes_nothing(tmp_path):
    svc = _svc(tmp_path)
    svc._write_run_step(
        run_id="", agent_id="coder", model="m", decision={"action": "x"}, state=_state()
    )
    assert list(tmp_path.glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_loop_writes_step_then_summary(tmp_path, monkeypatch):
    """Seam test: a real step_agent_stream run must actually invoke the capture.

    The unit tests above call _write_run_step directly, so they do NOT prove the
    call site in the loop fires. This drives the real loop with a faked decision
    (one read-only tool, then final) and asserts the step record lands BEFORE the
    summary — the silent-no-op failure mode a unit test cannot catch.
    """
    from runtime_v2.api import agent_service_v2 as _asv2

    monkeypatch.setattr(_asv2, "ANALYSIS_AGENTS", ())

    svc = _asv2.AgentServiceV2(orchestrator=None)
    svc._TRAJ_DIR = tmp_path

    seen = {"n": 0}

    async def decide(
        agent_id, model, messages, allowed_tools, prompt, turn, state, research_discharged
    ):
        seen["n"] += 1
        if seen["n"] == 1:
            return {"action": "filesystem", "operation": "list", "path": "."}
        return {"action": "final", "response": "Listed the working directory; done."}

    svc._get_decision = decide

    async for _ in svc.step_agent_stream("coder", "list the working directory"):
        pass

    files = list(tmp_path.glob("*.jsonl"))
    assert files, "seam: no trajectory file written"
    recs = _steps(files[0])
    assert recs[0]["record_type"] == "step"
    assert recs[0]["tool_calls"][0]["function_name"] == "filesystem"
    assert recs[0]["observation"]["results"][0]["extra"]["label"] == "NORMAL_SUCCESS"
    assert recs[-1]["record_type"] == "summary"
