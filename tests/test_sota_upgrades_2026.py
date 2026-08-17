from pathlib import Path
from rich.console import Console
from organism_console.state_store import SessionState
from organism_console.command_registry import registry, CommandContext
from swarm_os.services.chat_service import ChatService


def test_time_travel_checkpoints_api(tmp_path: Path):
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    state.history = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "response 1"},
    ]
    state.active_agent = "coordinator"
    state.save(sync=True)

    # Create checkpoint
    assert state.create_checkpoint("checkpoint_1") is True
    assert "checkpoint_1" in state.checkpoints

    # Mutate state
    state.history.append({"role": "user", "content": "turn 2"})
    state.active_agent = "coder"
    state.save(sync=True)
    assert len(state.history) == 3
    assert state.active_agent == "coder"

    # Rollback checkpoint
    assert state.rollback_checkpoint("checkpoint_1") is True
    assert len(state.history) == 2
    assert state.history[0]["content"] == "turn 1"
    assert state.active_agent == "coordinator"


def test_time_travel_checkpoints_cli(tmp_path: Path):
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    state.history = [{"role": "user", "content": "start"}]
    state.save(sync=True)

    console = Console()
    ctx = CommandContext(
        state=state,
        console=console,
        call_api=lambda *a, **kw: None,
        run_prompt=lambda *a, **kw: None,
        get_system_stats=lambda: {},
        installed_models=[],
    )

    # Test /checkpoint
    registry.handle_line("/checkpoint baseline", ctx)
    assert "baseline" in state.checkpoints

    # Test /checkpoints listing
    registry.handle_line("/checkpoints", ctx)

    # Modify and /rollback
    state.history.append({"role": "user", "content": "divergent turn"})
    registry.handle_line("/rollback baseline", ctx)
    assert len(state.history) == 1
    assert state.history[0]["content"] == "start"


def test_compact_context_messages():
    messages = [{"role": "system", "content": "System instructions"}]
    for i in range(1, 21):
        messages.append({"role": "user", "content": f"User prompt {i}"})
        messages.append({"role": "assistant", "content": f"Assistant response {i}"})

    assert len(messages) == 41

    compacted = ChatService.compact_context_messages(
        messages, max_turns=10, keep_recent=6
    )

    # Must contain original system prompt + 1 compacted summary + 6 recent non-system turns
    assert len(compacted) == 8
    assert compacted[0]["role"] == "system"
    assert compacted[0]["content"] == "System instructions"
    assert compacted[1]["role"] == "system"
    assert "<COMPACTED_SUMMARY>" in compacted[1]["content"]
    assert "earlier turns compacted" in compacted[1]["content"]
    assert compacted[-1]["content"] == "Assistant response 20"
