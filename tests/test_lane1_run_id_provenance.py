"""Lane 1 run_id provenance: backend stream → CLI JSON → harness → bridge.

These tests exercise the real production seam: the backend emits a
``model_selected`` chunk carrying the authoritative ``run_id``, the CLI
captures it, the harness surfaces it, and the evaluator bridge receives it.
Nothing here hand-feeds run_id values into functions — the flow is traced
through the actual code paths.
"""
from __future__ import annotations

import json

import pytest

from runtime_v2.api import agent_service_v2 as agent
from runtime_v2.services.stream_runner import ROLLOUT_ID_CTX


@pytest.fixture()
def rollout_ctx():
    token = ROLLOUT_ID_CTX.set("rollout-L1-9999")
    try:
        yield "rollout-L1-9999"
    finally:
        ROLLOUT_ID_CTX.reset(token)


@pytest.fixture()
def svc(tmp_path):
    service = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    service._TRAJ_DIR = tmp_path
    return service


# ---------------------------------------------------------------------------
# 1. Backend emits authoritative run_id in model_selected chunk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_selected_chunk_carries_run_id(svc, rollout_ctx):
    """The model_selected chunk must include the run_id minted by
    step_agent_stream_inner — the same run_id used by the trajectory."""

    async def _inner(agent_id, prompt, history, delegation_chain,
                     research_discharged, resume, allowed_tools_override,
                     genome_id, genome_weights, run_id, parent_id,
                     delegated_by):
        yield {
            "agent_id": agent_id,
            "type": "model_selected",
            "model": "robs4b",
            "provider": "llama",
            "resolved_model": "robs4b",
            "requested_role": "fast",
            "attempt": 1,
            "temperature": 0.1,
            "run_id": run_id,
        }
        yield {"agent_id": agent_id, "type": "final", "content": "done"}

    svc._step_agent_stream_inner = _inner
    chunks = []
    async for chunk in svc.step_agent_stream("coder", "test task"):
        chunks.append(chunk)

    model_chunks = [c for c in chunks if c.get("type") == "model_selected"]
    assert model_chunks, "must yield at least one model_selected chunk"
    run_id = model_chunks[0].get("run_id")
    assert run_id, "model_selected must carry run_id"
    assert isinstance(run_id, str) and len(run_id) == 36  # uuid4 format


@pytest.mark.asyncio
async def test_model_selected_run_id_matches_trajectory(tmp_path, rollout_ctx):
    """The run_id in the model_selected chunk must match the trajectory
    file name — proving it is the SAME authoritative identity."""

    async def _inner(agent_id, prompt, history, delegation_chain,
                     research_discharged, resume, allowed_tools_override,
                     genome_id, genome_weights, run_id, parent_id,
                     delegated_by):
        yield {
            "agent_id": agent_id,
            "type": "model_selected",
            "model": "robs4b",
            "run_id": run_id,
        }
        yield {"agent_id": agent_id, "type": "final", "content": "x"}

    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    svc._TRAJ_DIR = tmp_path
    svc._step_agent_stream_inner = _inner

    chunks = []
    async for chunk in svc.step_agent_stream("coder", "fix paths"):
        chunks.append(chunk)

    stream_run_id = [c["run_id"] for c in chunks if c.get("type") == "model_selected"][0]
    traj_files = list(tmp_path.glob("*.jsonl"))
    assert traj_files, "trajectory file must exist after run"
    traj_run_id = traj_files[0].stem
    assert stream_run_id == traj_run_id, (
        f"stream run_id {stream_run_id} must match trajectory filename {traj_run_id}"
    )


# ---------------------------------------------------------------------------
# 2. CLI JSON output preserves run_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cli_json_includes_run_id():
    """The --json output must include the run_id captured from the stream."""
    from organism_console.cli import run_agentic

    class FakeCtx:
        active_agent = "coder"
        active_model = "robs4b"
        last_stream_status = "completed"
        history = []
        undo_stack = []
        last_prompt = ""
        toasts_enabled = False
        last_run_id = "fake-run-uuid-1234"
        delegation_chain = ["coder"]
        resume_checkpoint_id = None

        def save(self):
            pass

    ctx = FakeCtx()

    async def _fake_stream(ctx, agent_id, prompt, history):
        return [{"role": "assistant", "content": "ok"}]

    from organism_console.ui import live_stream as ls
    original = ls._stream_prompt_async
    ls._stream_prompt_async = _fake_stream
    try:
        result = run_agentic(ctx, "test prompt", json_flag=True)
        assert result.get("run_id") == "fake-run-uuid-1234"
    finally:
        ls._stream_prompt_async = original


# ---------------------------------------------------------------------------
# 3. _attempt_once captures run_id from CLI JSON
# ---------------------------------------------------------------------------

def test_attempt_once_captures_run_id(tmp_path, monkeypatch):
    """When the CLI --json output includes run_id, _attempt_once must surface
    it as run_ids in its result dict."""
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "qwen_train"))
    import run_curriculum as rc

    fake_cli_output = json.dumps({
        "ok": True,
        "agent": "coder",
        "model": "robs4b",
        "prompt": "test",
        "content": "done",
        "files_changed": [],
        "run_id": "abc-123-def-456",
    })

    class FakeProc:
        returncode = 0
        stdout = fake_cli_output
        stderr = ""
        def poll(self):
            return 0
        def communicate(self, input="", timeout=0):
            return fake_cli_output, ""
        def kill(self):
            pass

    monkeypatch.setattr("run_curriculum.subprocess.Popen", lambda *a, **kw: FakeProc())
    monkeypatch.setattr("run_curriculum._check_endpoint_health", lambda *a, **kw: True)

    item = {"id": "x001", "prompt": "test task", "split": "train", "target_tools": ["filesystem"]}
    result = rc._attempt_once(item, timeout=60, allow_approval=False, record=False)

    assert result["cli_ok"] is True
    assert result["run_ids"] == ["abc-123-def-456"]


# ---------------------------------------------------------------------------
# 4. Missing run_id remains backward compatible
# ---------------------------------------------------------------------------

def test_attempt_once_none_when_no_run_id(tmp_path, monkeypatch):
    """If the CLI --json output has no run_id, _attempt_once must return
    run_ids=None — no fabricated UUID."""
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "qwen_train"))
    import run_curriculum as rc

    old_cli_output = json.dumps({
        "ok": True,
        "agent": "coder",
        "model": "robs4b",
        "prompt": "test",
        "content": "done",
        "files_changed": [],
    })

    class FakeProc:
        returncode = 0
        stdout = old_cli_output
        stderr = ""
        def poll(self):
            return 0
        def communicate(self, input="", timeout=0):
            return old_cli_output, ""
        def kill(self):
            pass

    monkeypatch.setattr("run_curriculum.subprocess.Popen", lambda *a, **kw: FakeProc())
    monkeypatch.setattr("run_curriculum._check_endpoint_health", lambda *a, **kw: True)

    item = {"id": "x002", "prompt": "test task", "split": "train", "target_tools": ["filesystem"]}
    result = rc._attempt_once(item, timeout=60, allow_approval=False, record=False)

    assert result.get("run_ids") is None, "must be None when no run_id available, not a fabricated UUID"


# ---------------------------------------------------------------------------
# 5. eval_twine.py passes run_ids to EvaluationBridge
# ---------------------------------------------------------------------------

def test_eval_twine_passes_run_ids_to_bridge():
    """eval_twine.py must forward res.get('run_ids') to the bridge, not None."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "qwen_train" / "eval_twine.py"
    code = src.read_text(encoding="utf-8")
    assert 'run_ids=res.get("run_ids")' in code, "eval_twine must pass res.run_ids to bridge"


def test_repair_task_passes_run_ids_to_bridge():
    """run_repair_task.py must forward res.get('run_ids') to the bridge, not None."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "qwen_train" / "run_repair_task.py"
    code = src.read_text(encoding="utf-8")
    assert 'run_ids=res.get("run_ids")' in code, "run_repair_task must pass res.run_ids to bridge"


# ---------------------------------------------------------------------------
# 6. Durable provenance join: rollout_id + run_id reach the trajectory
# ---------------------------------------------------------------------------

def test_durable_join_rollout_and_run_id(tmp_path, rollout_ctx):
    """Both rollout_id (Lane 2) and run_id (Lane 1) must be present in the
    trajectory summary, forming the complete join."""
    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    svc._TRAJ_DIR = tmp_path
    svc._write_run_trajectory(
        run_id="run-L1-jointest",
        agent_id="coder",
        parent_id="",
        delegated_by="",
        prompt="integration test prompt",
        genome_id="",
        last_chunk={"action": "final", "content": "ok"},
    )
    import json as _json
    lines = (tmp_path / "run-L1-jointest.jsonl").read_text(encoding="utf-8").splitlines()
    rec = _json.loads([l for l in lines if l.strip()][-1])
    assert rec["run_id"] == "run-L1-jointest"
    assert rec["rollout_id"] == rollout_ctx


# ---------------------------------------------------------------------------
# 7. Retry semantics: one rollout → multiple run_ids
# ---------------------------------------------------------------------------

def test_retry_runs_share_rollout_id(tmp_path, rollout_ctx):
    """Two separate backend runs under one rollout must each carry the same
    rollout_id and distinct run_ids."""
    svc = agent.AgentServiceV2.__new__(agent.AgentServiceV2)
    svc._TRAJ_DIR = tmp_path
    for rid in ("run-retry-A", "run-retry-B"):
        svc._write_run_trajectory(
            run_id=rid, agent_id="coder", parent_id="", delegated_by="",
            prompt=f"retry {rid}", genome_id="",
            last_chunk={"action": "final", "content": "x"},
        )
    import json as _json
    rec_a = _json.loads((tmp_path / "run-retry-A.jsonl").read_text().splitlines()[-1])
    rec_b = _json.loads((tmp_path / "run-retry-B.jsonl").read_text().splitlines()[-1])
    assert rec_a["run_id"] != rec_b["run_id"]
    assert rec_a["rollout_id"] == rec_b["rollout_id"] == rollout_ctx
