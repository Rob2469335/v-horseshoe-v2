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
    state.save(sync=True)
    
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
        installed_models=["qwen3.5-4b"]
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
        {"role": "coordinator", "content": "explain quantum computing", "response": "quantum computing uses qubits..."}
    ]
    state.save(sync=True)
    
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
        {"role": "coordinator", "content": "hello", "response": "world", "timestamp": 123456789.0}
    ]
    state.save(sync=True)
    
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
    log_files = list((Path(__file__).parent.parent / "swarm_os" / "logs").glob("trace_export_*.md"))
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
    state.save(sync=True)
    
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
        if "/agents/coordinator" in endpoint or endpoint == "/generate":
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
    rich.prompt.Confirm.ask = lambda *a, **k: True
    
    # Pre-clean any leftover dummy tool from prior runs or aborted tests
    sandbox_file = Path(__file__).parent.parent / "swarm_os" / "sandbox_tools" / "test_dummy.py"
    target_file = Path(__file__).parent.parent / "swarm_os" / "capabilities" / "test_dummy.py"
    for _path in (sandbox_file, target_file):
        if _path.exists():
            try:
                _path.unlink()
            except Exception:
                pass

    try:
        registry.handle_line("/tools create test_dummy", ctx)
        
        # Verify the file is created in sandbox_tools
        assert sandbox_file.exists()
        
        # Move to capabilities for testing router (handle Windows file existence cleanly)
        if target_file.exists():
            try:
                target_file.unlink()
            except Exception:
                pass
        import shutil
        shutil.move(str(sandbox_file), str(target_file))
        
        # Verify capability router dynamically loads it
        from swarm_os.capabilities.capability_router import CapabilityRouter
        router = CapabilityRouter()
        assert "test_dummy" in router.list_capabilities()
        
        handler = router.get_handler("test_dummy")
        assert handler.__class__.__name__ == "TestDummyHandler"
        
    finally:
        # Clean up
        import os
        import sys
        if 'target_file' in locals() and target_file.exists():
            try:
                os.remove(target_file)
            except Exception:
                pass
        if 'sandbox_file' in locals() and sandbox_file.exists():
            try:
                os.remove(sandbox_file)
            except Exception:
                pass
        sys.modules.pop("swarm_os.capabilities.test_dummy", None)
        rich.prompt.Prompt.ask = original_ask


def test_new_cli_commands(tmp_path, monkeypatch):
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    state.history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
        {"role": "user", "content": "foo"},
        {"role": "assistant", "content": "bar"},
        {"role": "user", "content": "baz"},
        {"role": "assistant", "content": "qux"},
    ]
    state.save(sync=True)
    console = Console()
    
    # Mock call_api
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self.json_data = json_data
            self.status_code = status_code
        def json(self):
            return self.json_data
            
    def mock_call_api(endpoint, method="GET", payload=None, stream=False):
        if "/agents/coordinator" in endpoint or endpoint == "/generate":
            return MockResponse({"response": "feat(core): dummy commit message\nDetailed body"})
        if endpoint == "/agents":
            return MockResponse([{"id": "coordinator", "role": "coordinator", "description": "dummy"}])
        return MockResponse({})
        
    ctx = CommandContext(
        state=state,
        console=console,
        call_api=mock_call_api,
        run_prompt=lambda *a: None,
        get_system_stats=lambda: {},
        installed_models=["model_a", "model_b"]
    )
    
    # 1. Mock subprocess.run
    import subprocess
    class MockCompletedProcess:
        def __init__(self, stdout="", stderr="", returncode=0):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode
            
    def mock_run(cmd, *args, **kwargs):
        if cmd[0] == "git" and "diff" in cmd:
            return MockCompletedProcess(stdout="diff content")
        if cmd[0] == "git" and "branch" in cmd:
            return MockCompletedProcess(stdout="main")
        return MockCompletedProcess()
        
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    # 2. Mock rich.prompt.Confirm.ask to always return False
    import rich.prompt
    monkeypatch.setattr(rich.prompt.Confirm, "ask", lambda *a, **k: False)
    
    # 3. Test /commit
    registry.handle_line("/commit", ctx)
    
    # 4. Test /branch
    registry.handle_line("/branch", ctx)
    registry.handle_line("/branch test-branch", ctx)
    
    # 5. Test /debug
    registry.handle_line("/debug echo hello", ctx)
    
    # 6. Test /prompt
    # Change project root temporarily for mandates_file
    import organism_console.command_registry as reg_mod
    original_path = reg_mod.Path
    class MockPath:
        def __init__(self, *args):
            self._real_path = original_path(*args)
        def __truediv__(self, other):
            if other == "docs":
                return tmp_path
            return MockPath(self._real_path / other)
        def __getattr__(self, item):
            return getattr(self._real_path, item)
        def resolve(self):
            return self
    monkeypatch.setattr(reg_mod, "Path", lambda *a: MockPath(*a) if a else original_path())
    
    registry.handle_line("/prompt coordinator", ctx)
    registry.handle_line("/prompt coordinator Be polite.", ctx)
    
    # 7. Test /memory
    class MockRequests:
        @staticmethod
        def post(url, json=None, **kwargs):
            if "embeddings" in url:
                return MockResponse({"embedding": [0.1] * 768})
            # search
            return MockResponse({"result": [{"score": 0.99, "payload": {"text": "hello"}}]})
        @staticmethod
        def put(url, json=None, **kwargs):
            return MockResponse({})
            
    import sys
    monkeypatch.setitem(sys.modules, "requests", MockRequests)
    
    registry.handle_line("/memory query test", ctx)
    registry.handle_line("/memory inject hello", ctx)
    
    # 8. Test /benchmark
    registry.handle_line("/benchmark", ctx)
    
    # 9. Test /compress
    registry.handle_line("/compress", ctx)


def test_natural_language_intent_routing(tmp_path, monkeypatch):
    import requests
    from organism_console.state_store import SessionState
    from organism_console.command_registry import registry, CommandContext
    
    session_file = tmp_path / ".session.json"
    state = SessionState(session_file)
    console = Console()
    
    calls = []
    def mock_call_api(endpoint, method="GET", payload=None, stream=False):
        calls.append(("api", endpoint, method))
        class MockResp:
            def json(self):
                if endpoint == "/readyz":
                    return {"ready": True, "status": "ok", "health_score": 100, "checks": {"llamacpp_reachable": True, "ollama_reachable": True}}
                if endpoint == "/generate":
                    return {"response": '{"command": "/status", "confidence": 0.95}'}
                return {}
            @property
            def status_code(self):
                return 200
        return MockResp()
        
    ctx = CommandContext(
        state=state,
        console=console,
        call_api=mock_call_api,
        run_prompt=lambda *a: None,
        get_system_stats=lambda: {"cpu": 10.0, "ram_pct": 50.0, "ram_color": "green", "ram_used_gb": 8.0, "ram_total_gb": 16.0},
        installed_models=["qwen3.5-4b"]
    )
    
    import subprocess
    class MockCompletedProcess:
        def __init__(self, stdout="", stderr="", returncode=0):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode
            
    def mock_run(cmd, *args, **kwargs):
        if cmd[0] == "git" and "diff" in cmd:
            calls.append("git_diff")
            return MockCompletedProcess(stdout="diff content")
        return MockCompletedProcess()
        
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    # 1. Test fast-path routing keyword (should call git diff immediately)
    res = registry.handle_line("what changed", ctx)
    assert res is None
    assert "git_diff" in calls
    
    # 2. Test fast-path goal routing (should execute /goal fix bugs)
    calls.clear()
    import rich.prompt
    monkeypatch.setattr(rich.prompt.Confirm, "ask", lambda *a, **k: False)
    res = registry.handle_line("fix the login page routing", ctx)
    assert "fix the login page routing" in state.command_history[-1]
    
    # 3. Test LLM classification routing
    calls.clear()
    
    res = registry.handle_line("how is my system looking", ctx)
    assert res is None
    assert any(c[0] == "api" and c[1] == "/generate" for c in calls)
    assert any(c[0] == "api" and c[1] == "/readyz" for c in calls)


def test_skill_memory_engine_tolerates_missing_fastembed():
    """Regression: the /upgrade command chain (SelfImprovementAgent ->
    SkillMemoryEngine) hard-crashed on `from fastembed import TextEmbedding`
    because fastembed is not a declared dependency. The import must be tolerant
    so constructing the engine (and running /upgrade's skill phase) does not
    raise ModuleNotFoundError — embed() surfaces a clear error instead."""
    import swarm_os.memory.intelligence.skill_memory_engine as m

    assert hasattr(m, "TextEmbedding")  # either the real class or None
    engine = m.SkillMemoryEngine()
    assert engine.embedder is None or m.TextEmbedding is not None
    if engine.embedder is None:
        import pytest
        with pytest.raises(RuntimeError):
            engine.embed("some pattern")



