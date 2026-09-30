"""Experiment J F1 infrastructure repair — regression seam tests (D1 + D2).

D1 (CLI fresh-run isolation):
    A brand-new one-shot / `--json` invocation must NOT forward a persisted
    stale `resume_checkpoint_id` to the backend. A stale CLI-session pointer
    (persisted in `.session.json`) can name a checkpoint from a DIFFERENT
    task/run/workspace (checkpoint_id derives only from agent+prompt) and,
    when the backend honors it, silently replaces the current prompt/state/run
    identity — the exact contamination observed on F1 Run 1.

    The real seam: `cli.run_agentic()` builds the request history that
    `live_stream` streams; the outgoing `/agents/{agent}/step/stream` payload
    carries `resume = ctx.resume_checkpoint_id` (live_stream.py:292). D1
    guarantees that for `allow_resume=False` the pointer is cleared BEFORE the
    payload is built, so resume is omitted/None.

D2 (backend defense-in-depth):
    The backend `_step_agent_stream_inner` resume-load block loads the checkpoint
    by the SUPPLIED resume id (the checkpointing contract is stored-ID
    authoritative). It NEVER re-derives identity from the caller-supplied prompt
    text — the production approval/ask_user continuation seam intentionally sends
    prompt="" with resume=<stored checkpoint id>. Instead, after loading, it
    validates the checkpoint's INTERNAL identity/context:
      - the stored/computed checkpoint id must be consistent with the resume;
      - stored agent_id (when recorded) must match the current agent;
      - stored workspace_root (when recorded) must match the current
        SWARM_WORKSPACE_ROOT.
    A mismatch is a FOREIGN/corrupt checkpoint: ignore it, log clearly, and start
    fresh — never silently replacing the current prompt/state/run identity.
    A legitimate continuation (prompt="", matching stored id) resumes normally.
"""

from __future__ import annotations

import pytest

import organism_console.cli as cli


# ── D1: CLI fresh-run isolation (the single-shot / --json path) ─────────────
def test_cli_fresh_run_clears_stale_resume_pointer(monkeypatch):
    """A fresh run_agentic (allow_resume=False) must clear a persisted stale
    resume_checkpoint_id BEFORE the request payload is built.

    We capture the value `live_stream` would place in the outgoing
    /step/stream payload, which is exactly `ctx.resume_checkpoint_id`
    (live_stream.py:292) — i.e. the real seam, not a helper in isolation.
    """
    from organism_console.state_store import SessionState
    from pathlib import Path

    state = SessionState(Path("nope-session.json"))
    # Simulate a persisted stale pointer from a prior session (the F1 Run 1
    # contamination source). It must NOT be forwarded on a fresh run.
    state.resume_checkpoint_id = "dc6511c694a88559"
    captured = {}

    def _fake_stream_prompt_with_retry(ctx, agent_id, prompt, history, max_retries=3):
        # The payload builder reads ctx.resume_checkpoint_id at this boundary.
        captured["resume"] = ctx.resume_checkpoint_id
        captured["agent"] = agent_id
        captured["prompt"] = prompt
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(cli, "stream_prompt_with_retry", _fake_stream_prompt_with_retry)

    result = cli.run_agentic(
        state, "Work inside this repository - fix sandbox_bounds", json_flag=True
    )

    assert captured["resume"] is None, (
        "fresh run must not forward a stale resume_checkpoint_id"
    )
    assert result.get("ok") is True


def test_cli_explicit_continue_preserves_resume_pointer(monkeypatch):
    """An explicitly-requested continuation (--continue / allow_resume=True)
    must KEEP the resume pointer so a genuine interrupted-run resume still
    reaches the backend."""
    from organism_console.state_store import SessionState
    from pathlib import Path

    state = SessionState(Path("nope-session.json"))
    state.resume_checkpoint_id = "legit-ckpt-123"
    captured = {}

    def _fake_stream_prompt_with_retry(ctx, agent_id, prompt, history, max_retries=3):
        captured["resume"] = ctx.resume_checkpoint_id
        return [{"role": "assistant", "content": "resumed"}]

    monkeypatch.setattr(cli, "stream_prompt_with_retry", _fake_stream_prompt_with_retry)

    cli.run_agentic(
        state, "continue this goal", json_flag=True, allow_resume=True
    )

    assert captured["resume"] == "legit-ckpt-123", (
        "explicit --continue must preserve the resume pointer"
    )


# ── D2: backend resume-context guard (the streaming seam) ───────────────────
def _make_service():
    from unittest.mock import AsyncMock, MagicMock

    from runtime_v2.api.agent_service_v2 import AgentServiceV2

    svc = AgentServiceV2.__new__(AgentServiceV2)
    svc.event_store = MagicMock()
    svc.event_store.append = MagicMock()
    svc.orchestrator = MagicMock()
    svc.orchestrator.events = svc.event_store
    svc.orchestrator.router = MagicMock()
    svc.orchestrator.router.route_model = AsyncMock(
        return_value=MagicMock(model="robs4b")
    )
    svc.orchestrator.bridge = MagicMock()
    svc.orchestrator.bridge.get_relevant_memories = AsyncMock(return_value="")
    svc.orchestrator.mcp = MagicMock()
    svc.orchestrator.mcp.get_tool_schemas_for_agent.return_value = []
    svc._prompt_repairer = MagicMock()
    svc._prompt_repairer.check_for_past_mistakes = AsyncMock(return_value="")
    svc._agents = {"coder": {"tools": ["filesystem"]}}
    svc._fitness_env_enabled = MagicMock(return_value=False)
    svc._feed_outcome = MagicMock()
    svc._feed_aborted_outcome = MagicMock()
    svc._record_event = MagicMock()
    svc._record_success = MagicMock()
    svc.get_lesson_manager = MagicMock(
        return_value=MagicMock(
            render_active_lessons=AsyncMock(return_value=""),
            get_all=AsyncMock(return_value=[]),
        )
    )
    return svc


@pytest.mark.asyncio
async def test_foreign_resume_checkpoint_mismatched_workspace_is_refused(
    monkeypatch, tmp_path
):
    """FOREIGN checkpoint: a supplied resume id naming a checkpoint whose stored
    identity is self-consistent BUT whose workspace_root differs from the current
    backend SWARM_WORKSPACE_ROOT must be refused — never replacing the current
    prompt/state/run identity.

    This reproduces the original Run-1 contamination shape: the stale CLI pointer
    named a twine-era checkpoint written under a DIFFERENT workspace than the F1
    run's clone. The checkpoint is refused and the run starts fresh."""
    import runtime_v2.api.agent_service_v2 as mod
    from runtime_v2.services import checkpointing as ck

    monkeypatch.setattr(ck, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    monkeypatch.setattr(mod.AgentServiceV2, "_TRAJ_DIR", tmp_path / "trajectories")

    # Backend serves the Run-1 clone.
    run1_ws = "C:\\Users\\rober\\Projects\\swe_probe_work\\f1_pilot\\run_1\\repo"
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", run1_ws)

    # Stored FOREIGN checkpoint: internally self-consistent (its own id), but
    # written under a DIFFERENT workspace (the twine clone — the Run-1 source).
    foreign_prompt = (
        "Work inside this repository - your filesystem tools can read and write "
        "under it:\nC:\\Users\\rober\\Projects\\swe_probe_work\\pypa__twine-1066\\repo"
    )
    foreign_id = ck.checkpoint_id("coder", foreign_prompt)
    ck.write_checkpoint(
        foreign_id,
        {
            "checkpoint_id": foreign_id,
            "_computed_checkpoint_id": foreign_id,
            "agent_id": "coder",
            "prompt": foreign_prompt,
            "turn": 13,
            "messages": [{"role": "user", "content": "foreign task"}],
            "state": {
                "run_id": "0423c8fe-71a1-4a61-b435-e8ccab38e1f2",
                "_turn": 14,
                "_step_seq": 8,
                "read_paths": [".gitignore", "AGENTS.md"],
            },
            "loop_guards": {},
            "workspace_root": (
                "C:\\Users\\rober\\Projects\\swe_probe_work\\pypa__twine-1066\\repo"
            ),
        },
    )

    svc = _make_service()

    async def _fake_lookup_model(agent_id):
        return ("robs4b", "llama")

    monkeypatch.setattr(
        "runtime_v2.api.agent_service_v2.lookup_model", _fake_lookup_model
    )

    current_prompt = (
        "Work inside this repository - your filesystem tools can read and write "
        "under it:\nC:\\Users\\rober\\Projects\\swe_probe_work\\f1_pilot\\run_1\\repo"
        "\n\nProblem:\nsandbox_bounds reports incorrect write_covers_workspace "
        "for subfolder write roots"
    )

    gen = svc.step_agent_stream("coder", current_prompt, resume=foreign_id)
    marker = None
    try:
        async for chunk in gen:
            if chunk.get("type") == "resumed":
                marker = chunk
                break
    finally:
        await gen.aclose()

    assert marker is not None, "expected a resumed marker from the guard"
    assert marker.get("fresh") is True, (
        "foreign-workspace checkpoint must be refused (fresh), not resumed — "
        f"got {marker}"
    )
    assert "workspace" not in str(marker.get("prompt", "")).lower() or "twine" not in (
        marker.get("prompt", "") or ""
    ), "foreign stored prompt must not replace the current prompt"


@pytest.mark.asyncio
async def test_foreign_resume_checkpoint_stored_id_mismatch_is_refused(
    monkeypatch, tmp_path
):
    """A foreign checkpoint whose STORED id / computed id contradicts the supplied
    resume must be refused (internally inconsistent / corrupt data)."""
    import runtime_v2.api.agent_service_v2 as mod
    from runtime_v2.services import checkpointing as ck

    monkeypatch.setattr(ck, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    monkeypatch.setattr(mod.AgentServiceV2, "_TRAJ_DIR", tmp_path / "trajectories")
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "")

    # Checkpoint stored under a DIFFERENT id than the id the resume name uses.
    wrong_prompt = "some other task"
    stored_id = ck.checkpoint_id("coder", wrong_prompt)
    ck.write_checkpoint(
        stored_id,
        {
            "checkpoint_id": stored_id,
            "agent_id": "coder",
            "prompt": wrong_prompt,
            "turn": 5,
            "messages": [{"role": "user", "content": wrong_prompt}],
            "state": {},
            "loop_guards": {},
        },
    )
    # Supplying a resume id that does not match the stored content's own id.
    bogus_resume = ck.checkpoint_id("coder", "current sandbox prompt here")

    svc = _make_service()

    async def _fake_lookup_model(agent_id):
        return ("robs4b", "llama")

    monkeypatch.setattr(
        "runtime_v2.api.agent_service_v2.lookup_model", _fake_lookup_model
    )

    gen = svc.step_agent_stream("coder", "current sandbox prompt here", resume=bogus_resume)
    marker = None
    try:
        async for chunk in gen:
            if chunk.get("type") == "resumed":
                marker = chunk
                break
    finally:
        await gen.aclose()

    assert marker is not None, "expected a resumed marker from the guard"
    assert marker.get("fresh") is True, (
        "stored-id-mismatched checkpoint must be refused (fresh), got {marker}"
    )


@pytest.mark.asyncio
async def test_real_legitimate_continuation_prompt_empty_resumes_stored_checkpoint(
    monkeypatch, tmp_path
):
    """REAL LEGITIMATE CONTINUATION (mandatory D case): the production approval /
    ask_user continuation seam (live_stream.py) sends `prompt=""` with
    `resume=<stored checkpoint id>`. The corrected D2 must load the checkpoint by
    that stored id and restore its stored prompt/state/run_id/turn — NOT refuse it
    by re-hashing the (empty) caller prompt."""
    import runtime_v2.api.agent_service_v2 as mod
    from runtime_v2.services import checkpointing as ck

    monkeypatch.setattr(ck, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    monkeypatch.setattr(mod.AgentServiceV2, "_TRAJ_DIR", tmp_path / "trajectories")
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "")

    stored_prompt = "fix the widget bug"
    cid = ck.checkpoint_id("coder", stored_prompt)
    ck.write_checkpoint(
        cid,
        {
            "checkpoint_id": cid,
            "_computed_checkpoint_id": cid,
            "agent_id": "coder",
            "prompt": stored_prompt,
            "turn": 7,
            "messages": [{"role": "user", "content": stored_prompt}],
            "state": {
                "run_id": "tracked-run-abc",
                "_turn": 8,
                "_step_seq": 6,
                "read_paths": ["swarm_os/lib/paths.py"],
            },
            "loop_guards": {},
            "workspace_root": "",
        },
    )

    svc = _make_service()

    async def _fake_lookup_model(agent_id):
        return ("robs4b", "llama")

    monkeypatch.setattr(
        "runtime_v2.api.agent_service_v2.lookup_model", _fake_lookup_model
    )

    # The REAL continuation shape: prompt="" + resume=<stored id>.
    gen = svc.step_agent_stream("coder", "", resume=cid)
    marker = None
    try:
        async for chunk in gen:
            if chunk.get("type") == "resumed":
                marker = chunk
                break
    finally:
        await gen.aclose()

    assert marker is not None, "expected a resumed marker"
    assert marker.get("fresh") is not True, (
        "prompt='' + matching stored id MUST resume, not fresh — got {marker}"
    )
    assert marker.get("from_turn") == 7, (
        "stored turn must be preserved (got %s)" % marker.get("from_turn")
    )
    assert marker.get("prompt", "").startswith(stored_prompt), (
        "stored prompt must be restored even when caller prompt is empty"
    )
    # Stored run_id must be preserved (not replaced by a fresh one).
    assert ck.load_checkpoint(cid)["state"]["run_id"] == "tracked-run-abc"


@pytest.mark.asyncio
async def test_workspace_mismatch_same_identity_refused(monkeypatch, tmp_path):
    """WORKSPACE MISMATCH: a checkpoint whose internal identity matches the
    supplied resume ID but whose recorded workspace_root differs from the current
    backend SWARM_WORKSPACE_ROOT must be refused-and-fresh."""
    import runtime_v2.api.agent_service_v2 as mod
    from runtime_v2.services import checkpointing as ck

    monkeypatch.setattr(ck, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    monkeypatch.setattr(mod.AgentServiceV2, "_TRAJ_DIR", tmp_path / "trajectories")

    prompt = "fix the widget bug"
    cid = ck.checkpoint_id("coder", prompt)
    cur_ws = "C:\\Users\\rober\\Projects\\swe_probe_work\\f1_pilot\\run_9\\repo"
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", cur_ws)
    ck.write_checkpoint(
        cid,
        {
            "checkpoint_id": cid,
            "_computed_checkpoint_id": cid,
            "agent_id": "coder",
            "prompt": prompt,
            "turn": 5,
            "messages": [{"role": "user", "content": prompt}],
            "state": {"run_id": "my-run", "_turn": 6, "_step_seq": 4},
            "loop_guards": {},
            "workspace_root": "C:\\Users\\rober\\Projects\\swe_probe_work\\OTHER_ws\\repo",
        },
    )

    svc = _make_service()

    async def _fake_lookup_model(agent_id):
        return ("robs4b", "llama")

    monkeypatch.setattr(
        "runtime_v2.api.agent_service_v2.lookup_model", _fake_lookup_model
    )

    gen = svc.step_agent_stream("coder", prompt, resume=cid)
    marker = None
    try:
        async for chunk in gen:
            if chunk.get("type") == "resumed":
                marker = chunk
                break
    finally:
        await gen.aclose()

    assert marker is not None, "expected a resumed marker"
    assert marker.get("fresh") is True, (
        "workspace-mismatched checkpoint must be refused (fresh), got {marker}"
    )