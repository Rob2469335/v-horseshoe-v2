# tests/test_cli_terminal.py
from __future__ import annotations

import json
from pathlib import Path
from rich.console import Console

from organism_console.state_store import SessionState
from organism_console.command_registry import registry, CommandContext
from organism_console.renderer import render_delegation_tree, render_step_micro_ui


def test_session_state_load_save(tmp_path: Path):
    session_file = tmp_path / ".session.json"
    
    state = SessionState(session_file)
    state.active_agent = "planner"
    state.trace_mode = True
    state.mode = "dev"
    state.history.append({"agent_id": "coordinator", "prompt": "hello", "response": "world"})
    state.save()
    
    # Reload and assert values are persistent
    reloaded = SessionState(session_file)
    assert reloaded.active_agent == "planner"
    assert reloaded.trace_mode is True
    assert reloaded.mode == "dev"
    assert len(reloaded.history) == 1
    assert reloaded.history[0]["prompt"] == "hello"


def test_command_registry_parsing(tmp_path: Path):
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    console = Console()
    
    calls = []
    
    def mock_call_api(endpoint, method, payload=None, stream=False):
        calls.append(("api", endpoint, method))
        return None
        
    def mock_run_prompt(prompt):
        calls.append(("prompt", prompt))
        
    def mock_get_system_stats():
        return {"cpu": 10.0, "ram_pct": 50.0, "ram_color": "green", "ram_used_gb": 8.0, "ram_total_gb": 16.0}

    ctx = CommandContext(
        state=state,
        console=console,
        call_api=mock_call_api,
        run_prompt=mock_run_prompt,
        get_system_stats=mock_get_system_stats,
        installed_models=["qwen2.5:7b"]
    )
    
    # 1. Non-command prompt passes straight through
    res1 = registry.handle_line("what is zenith?", ctx)
    assert res1 == "what is zenith?"
    
    # 2. Toggle trace command
    res2 = registry.handle_line("/trace on", ctx)
    assert res2 is None
    assert state.trace_mode is True
    
    # 3. Change agent command
    res3 = registry.handle_line("/agent coder", ctx)
    assert res3 is None
    assert state.active_agent == "coder"
    assert state.delegation_chain == ["coder"]
    
    # 4. Change mode command
    res4 = registry.handle_line("/mode dev", ctx)
    assert res4 is None
    assert state.mode == "dev"
    
    # 5. History and Replay commands
    state.history = [
        {"agent_id": "coordinator", "prompt": "explain quantum computing", "response": "quantum computing uses qubits..."}
    ]
    state.save()
    
    res5 = registry.handle_line("/replay 0", ctx)
    assert res5 == "explain quantum computing"
    assert state.active_agent == "coordinator"


def test_delegation_tree_rendering():
    # Empty chain
    assert render_delegation_tree([]) == "[dim](no active delegation chain)[/dim]"
    
    # Single level
    t1 = render_delegation_tree(["coordinator"])
    assert "coordinator" in t1
    
    # Nested level
    t2 = render_delegation_tree(["coordinator", "planner", "executor"])
    assert "coordinator" in t2
    assert "├── [cyan]planner[/cyan]" in t2
    assert "└── [cyan]executor[/cyan]" in t2


def test_step_micro_ui():
    r1 = render_step_micro_ui("thinking", "analyzing workspace")
    assert "🧠" in r1
    assert "Thinking" in r1
    assert "analyzing workspace" in r1
    
    r2 = render_step_micro_ui("model_selected", "using llama3")
    assert "🚀" in r2
    assert "Model_selected" in r2
    assert "using llama3" in r2


def test_goal_command_routing(tmp_path: Path):
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    console = Console()
    
    goal_invoked = []
    
    def mock_run_goal_loop(objective):
        goal_invoked.append(objective)

    ctx = CommandContext(
        state=state,
        console=console,
        call_api=lambda *a: None,
        run_prompt=lambda *a: None,
        get_system_stats=lambda: {},
        installed_models=[],
        run_goal_loop=mock_run_goal_loop
    )
    
    registry.handle_line("/goal fix tests/test_smoke.py", ctx)
    assert len(goal_invoked) == 1
    assert goal_invoked[0] == "fix tests/test_smoke.py"


def test_tokens_diff_and_export_commands(tmp_path: Path):
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    state.total_input_tokens = 500
    state.total_output_tokens = 1200
    state.history = [
        {"agent_id": "coordinator", "prompt": "hello", "response": "world", "timestamp": 123456789.0}
    ]
    state.save()
    
    console = Console()
    ctx = CommandContext(
        state=state,
        console=console,
        call_api=lambda *a: None,
        run_prompt=lambda *a: None,
        get_system_stats=lambda: {},
        installed_models=[]
    )
    
    # 1. /tokens command executes successfully
    registry.handle_line("/tokens", ctx)
    
    # 2. /diff command executes successfully
    registry.handle_line("/diff", ctx)
    
    # 3. /trace export command exports history
    import os
    registry.handle_line("/trace export", ctx)
    
    # Find exported file
    log_files = list(Path("swarm_os/logs").glob("trace_export_*.md"))
    assert len(log_files) >= 1
    # Clean up the created test file
    for f in log_files:
        try:
            os.remove(f)
        except Exception:
            pass


def test_debates_and_time_travel_commands(tmp_path: Path):
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    state.history = [
        {"agent_id": "coordinator", "prompt": "run 1", "response": "res 1", "timestamp": 1.0},
        {"agent_id": "planner", "prompt": "run 2", "response": "res 2", "timestamp": 2.0},
        {"agent_id": "reviewer", "prompt": "run 3", "response": "res 3", "timestamp": 3.0}
    ]
    state.history_pointer = -1
    state.save()
    
    console = Console()
    debate_invoked = []
    
    def mock_run_debate(goal):
        debate_invoked.append(goal)
        
    ctx = CommandContext(
        state=state,
        console=console,
        call_api=lambda *a: None,
        run_prompt=lambda *a: None,
        get_system_stats=lambda: {},
        installed_models=[],
        run_debate=mock_run_debate
    )
    
    # 1. /prev steps back in history
    registry.handle_line("/prev", ctx)
    assert state.history_pointer == 1
    
    # 2. /next steps forward in history
    registry.handle_line("/next", ctx)
    assert state.history_pointer == 2
    
    # 3. /debate runs debate loop callback
    registry.handle_line("/debate evaluate self-healing", ctx)
    assert len(debate_invoked) == 1
    assert debate_invoked[0] == "evaluate self-healing"


def test_ast_impact_command(tmp_path: Path):
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    console = Console()
    ctx = CommandContext(
        state=state,
        console=console,
        call_api=lambda *a: None,
        run_prompt=lambda *a: None,
        get_system_stats=lambda: {},
        installed_models=[]
    )
    
    # Run /impact on an existing file
    registry.handle_line("/impact organism_console/renderer.py", ctx)


def test_consensus_vote_command(tmp_path: Path):
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    console = Console()
    
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self.json_data = json_data
            self.status_code = status_code
            
        def json(self):
            return self.json_data
            
    def mock_call_api(endpoint, method, payload=None, stream=False):
        if endpoint == "/generate" and payload:
            model = payload.get("model", "unknown")
            return MockResponse({"response": f"Response from model {model} about quantum.", "model": model})
        return MockResponse({"installed_models": ["modelA", "modelB", "modelC"]})
        
    ctx = CommandContext(
        state=state,
        console=console,
        call_api=mock_call_api,
        run_prompt=lambda *a: None,
        get_system_stats=lambda: {},
        installed_models=["modelA", "modelB", "modelC"]
    )
    
    registry.handle_line("/vote what is quantum computing?", ctx)


def test_dynamic_tool_creation_command_and_router(tmp_path: Path):
    # Setup temporary environment to mock capabilities directory
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    state.mode = "dev" # bypass prompts
    console = Console()
    
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self.json_data = json_data
            self.status_code = status_code
            
        def json(self):
            return self.json_data
            
    # Mock capability generator code response
    mock_code = """
class TestDummyHandler:
    async def execute(self, payload):
        return {"status": "success", "val": "dummy"}
"""
    
    def mock_call_api(endpoint, method, payload=None, stream=False):
        if endpoint == "/generate":
            return MockResponse({"response": f"```python\n{mock_code}\n```"})
        return MockResponse({"capabilities": ["chat_search"], "count": 1})
        
    ctx = CommandContext(
        state=state,
        console=console,
        call_api=mock_call_api,
        run_prompt=lambda *a: None,
        get_system_stats=lambda: {},
        installed_models=[]
    )
    
    # Mock user input to description prompt
    import rich.prompt
    original_ask = rich.prompt.Prompt.ask
    rich.prompt.Prompt.ask = lambda *a, **k: "A dummy test tool"
    
    try:
        registry.handle_line("/tools create test_dummy", ctx)
        
        # Verify the file is created
        target_file = Path(__file__).parent.parent / "swarm_os" / "capabilities" / "test_dummy.py"
        assert target_file.exists()
        
        # Verify capability router dynamically loads it
        from swarm_os.capabilities.capability_router import CapabilityRouter
        router = CapabilityRouter()
        assert "test_dummy" in router.list_capabilities()
        
        handler = router.get_handler("test_dummy")
        assert handler.__class__.__name__ == "TestDummyHandler"
        
        # Clean up
        import os
        os.remove(target_file)
    finally:
        rich.prompt.Prompt.ask = original_ask

