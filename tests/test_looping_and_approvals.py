import pytest
import json
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from swarm_os.services.agent_service import AgentService
from swarm_os.core.orchestrator import Orchestrator
from swarm_os.exceptions import ApprovalRequiredError

# A helper mock context manager for httpx.AsyncClient.stream
class MockResponse:
    def __init__(self, lines):
        self._lines = lines
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    def raise_for_status(self):
        pass
    async def aiter_lines(self):
        for line in self._lines:
            yield line

@pytest.fixture
def mock_agent_service():
    orchestrator = Orchestrator()
    service = AgentService(orchestrator=orchestrator)
    # Ensure local ollama is chosen
    import os
    os.environ["ZENITH_MODEL"] = "qwen2.5:3b-instruct"
    return service

@pytest.mark.asyncio
async def test_a_no_tool_final_answer(mock_agent_service):
    """
    Test A: no-tool final answer
    Assert: exactly one final chunk, no repeated assistant blocks, stream ends once.
    """
    # Mock LLM output that doesn't contain a tool call
    lines = [
        json.dumps({"message": {"content": "Here is the final narrative answer."}}),
        json.dumps({"done": True})
    ]
    
    with patch("httpx.AsyncClient.stream", return_value=MockResponse(lines)):
        chunks = []
        async for chunk in mock_agent_service.step_agent_stream(
            agent_id="coordinator",
            prompt="Hello, who are you?",
            history=[]
        ):
            chunks.append(chunk)
            
        # Assert stream contains the text chunk and exactly one final type chunk
        text_chunks = [c for c in chunks if "content" in c and c.get("type") != "final"]
        final_chunks = [c for c in chunks if c.get("type") == "final"]
        
        assert len(text_chunks) > 0
        assert text_chunks[0]["content"] == "Here is the final narrative answer."
        assert len(final_chunks) == 1
        assert final_chunks[0]["content"] == "Here is the final narrative answer."

@pytest.mark.asyncio
async def test_b_read_only_tool_then_final_answer(mock_agent_service):
    """
    Test B: read-only tool then final answer
    Assert: exactly one allowed read-only tool call, exactly one final answer, stream ends cleanly.
    """
    # First turn: returns a read-only filesystem list tool call
    # Second turn: returns final answer
    lines_turn_1 = [
        json.dumps({"message": {"content": 'I will list the directory contents: <tool_call name="filesystem">{"operation": "list", "path": "."}</tool_call>'}}),
        json.dumps({"done": True})
    ]
    lines_turn_2 = [
        json.dumps({"message": {"content": "I see the files in the directory."}}),
        json.dumps({"done": True})
    ]
    
    mock_stream = MagicMock()
    mock_stream.side_effect = [
        MockResponse(lines_turn_1),
        MockResponse(lines_turn_2)
    ]
    
    with patch("httpx.AsyncClient.stream", mock_stream):
        chunks = []
        # Mock actual filesystem list implementation to be safe
        mock_agent_service.runtimes["coordinator"].call_tool = AsyncMock(return_value={"files": ["file1.txt", "file2.txt"]})
        
        async for chunk in mock_agent_service.step_agent_stream(
            agent_id="coordinator",
            prompt="What files are in the workspace?",
            history=[]
        ):
            chunks.append(chunk)
            
        # Verify tool was called once
        mock_agent_service.runtimes["coordinator"].call_tool.assert_called_once_with("filesystem", {"operation": "list", "path": "."})
        
        # Verify final answer was emitted
        final_chunks = [c for c in chunks if c.get("type") == "final"]
        assert len(final_chunks) == 1
        assert "files" in chunks[1]["result"]

@pytest.mark.asyncio
async def test_c_write_action_requires_approval(mock_agent_service):
    """
    Test C: write action requires approval
    Assert: no file is changed before approval, approval request is yielded, simulated approval triggers change.
    """
    # Proposing a write operation
    lines_proposal = [
        json.dumps({"message": {"content": 'I need to write hello to test.txt. <tool_call name="filesystem">{"operation": "write", "path": "test.txt", "content": "hello"}</tool_call>'}}),
        json.dumps({"done": True})
    ]
    
    # 1. First request: should trigger ask_user approval request
    with patch("httpx.AsyncClient.stream", return_value=MockResponse(lines_proposal)):
        chunks = []
        async for chunk in mock_agent_service.step_agent_stream(
            agent_id="coordinator",
            prompt="Create a file named test.txt containing hello",
            history=[]
        ):
            chunks.append(chunk)
            
        # Assert ask_user is yielded
        assert len(chunks) == 2  # content chunk + ask_user chunk
        assert "ask_user" in chunks[1]
        assert "APPROVAL REQUIRED" in chunks[1]["ask_user"]["question"]

    # 2. Simulate User Approval and second call
    lines_final = [
        json.dumps({"message": {"content": "I successfully created the file test.txt."}}),
        json.dumps({"done": True})
    ]
    
    history_after_approval = [
        {"role": "user", "content": "Create a file named test.txt containing hello"},
        {"role": "assistant", "content": 'I need to write hello to test.txt. <tool_call name="filesystem">{"operation": "write", "path": "test.txt", "content": "hello"}</tool_call>'},
        {"role": "user", "content": "Observation: {\"answer\": \"approve\"}"}
    ]
    
    mock_runtime_call = AsyncMock(return_value={"status": "success", "message": "Success writing to test.txt"})
    mock_agent_service.runtimes["coordinator"].call_tool = mock_runtime_call

    with patch("httpx.AsyncClient.stream", return_value=MockResponse(lines_final)):
        chunks = []
        async for chunk in mock_agent_service.step_agent_stream(
            agent_id="coordinator",
            prompt="",
            history=history_after_approval
        ):
            chunks.append(chunk)
            
        # Verify approved tool call executed immediately before LLM turn
        mock_runtime_call.assert_called_once_with("filesystem", {"operation": "write", "path": "test.txt", "content": "hello"})
        
        # Verify final answer is emitted
        final_chunks = [c for c in chunks if c.get("type") == "final"]
        assert len(final_chunks) == 1
        assert final_chunks[0]["content"] == "I successfully created the file test.txt."

@pytest.mark.asyncio
async def test_d_approval_denied(mock_agent_service):
    """
    Test D: approval denied
    Assert: no write occurs, returning safe non-executing response.
    """
    history_after_denial = [
        {"role": "user", "content": "Create a file named test.txt containing hello"},
        {"role": "assistant", "content": 'I need to write hello to test.txt. <tool_call name="filesystem">{"operation": "write", "path": "test.txt", "content": "hello"}</tool_call>'},
        {"role": "user", "content": "Observation: {\"answer\": \"deny\"}"}
    ]
    
    lines_denial_handling = [
        json.dumps({"message": {"content": "I will not write to the file since approval was denied."}}),
        json.dumps({"done": True})
    ]
    
    mock_runtime_call = AsyncMock()
    mock_agent_service.runtimes["coordinator"].call_tool = mock_runtime_call

    with patch("httpx.AsyncClient.stream", return_value=MockResponse(lines_denial_handling)):
        chunks = []
        async for chunk in mock_agent_service.step_agent_stream(
            agent_id="coordinator",
            prompt="",
            history=history_after_denial
        ):
            chunks.append(chunk)
            
        # Verify tool was NOT called
        mock_runtime_call.assert_not_called()
        
        # Verify final response is narrative and safe
        final_chunks = [c for c in chunks if c.get("type") == "final"]
        assert len(final_chunks) == 1
        assert "not write" in final_chunks[0]["content"]

@pytest.mark.asyncio
async def test_e_coordinator_repeated_content_regression(mock_agent_service):
    """
    Test E: coordinator repeated-content regression
    Assert: repeated identical or near-identical final content is terminated.
    """
    # Returns the exact same narrative in Turn 0 and Turn 1 (near-identical/identical)
    lines_turn_1 = [
        json.dumps({"message": {"content": "I am thinking about the Immediate Fixes..."}}),
        json.dumps({"done": True})
    ]
    lines_turn_2 = [
        json.dumps({"message": {"content": "I am thinking about the Immediate Fixes..."}}),
        json.dumps({"done": True})
    ]
    
    mock_stream = MagicMock()
    mock_stream.side_effect = [
        MockResponse(lines_turn_1),
        MockResponse(lines_turn_2)
    ]
    
    with patch("httpx.AsyncClient.stream", mock_stream):
        chunks = []
        # Make the limit high enough to trigger loop
        async for chunk in mock_agent_service.step_agent_stream(
            agent_id="coordinator",
            prompt="Plan fixes for the bug.",
            history=[]
        ):
            chunks.append(chunk)
            
        # Verify it terminated early because of duplicate narrative check in Turn 1
        # It should only have executed one call before breaking
        assert mock_stream.call_count == 1
        
        final_chunks = [c for c in chunks if c.get("type") == "final"]
        assert len(final_chunks) == 1

@pytest.mark.asyncio
async def test_f_client_consumer_stream_handling(mock_agent_service):
    """
    Test F: client/consumer stream handling
    Assert: CLI client stream-consumer correctly appends new history.
    """
    from organism_console.cli import CLIContext, stream_prompt
    import organism_console.cli as cli
    
    # Mock call_api to return mock NDJSON stream response
    mock_api_resp = MagicMock()
    mock_api_resp.iter_lines.return_value = [
        json.dumps({"content": "Hello client"}).encode("utf-8"),
        json.dumps({"type": "final", "content": "Final response text"}).encode("utf-8")
    ]
    
    cli.ctx = CLIContext()
    cli.ctx.history = []
    
    with patch("organism_console.cli.call_api", return_value=mock_api_resp):
        new_hist = stream_prompt(
            agent_id="coordinator",
            prompt="Greeting",
            history=[]
        )
        
        # Verify that prompt and final content are saved in the history
        assert len(new_hist) == 2
        assert new_hist[0] == {"role": "user", "content": "Greeting"}
        assert new_hist[1] == {"role": "assistant", "content": "Final response text"}

def test_g_syntax_check_fail_fast():
    """
    Test G: fast syntax check fail-fast mechanism
    Assert: syntax check fails and yields descriptive traceback.
    """
    from organism_console.cli import run_syntax_checks
    
    mock_git_diff = MagicMock()
    mock_git_diff.returncode = 0
    mock_git_diff.stdout = "modified_file.py\n"
    
    import py_compile
    # Mock compile to raise PyCompileError
    mock_compile = MagicMock(side_effect=py_compile.PyCompileError(SyntaxError, SyntaxError("invalid syntax"), "modified_file.py", "invalid syntax"))
    
    with patch("subprocess.run", return_value=mock_git_diff), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("py_compile.compile", mock_compile):
        passed, msg = run_syntax_checks()
        assert passed is False
        assert "invalid syntax" in msg
        assert "modified_file.py" in msg

def test_h_debug_command_auto_repair():
    """
    Test H: debug command automated repair loop
    Assert: failed execution prompts for auto-repair and starts the autonomous loop.
    """
    from organism_console.command_registry import cmd_debug
    
    mock_ctx = MagicMock()
    mock_ctx.console = MagicMock()
    mock_ctx.run_goal_loop = MagicMock()
    mock_ctx.state = MagicMock()
    mock_ctx.state.active_model = "test-model"
    
    # Mock subprocess.run to simulate crash
    mock_res = MagicMock()
    mock_res.returncode = 1
    mock_res.stdout = ""
    mock_res.stderr = "Traceback (most recent call last):\n  File \"crash.py\", line 2\n    import non_existent"
    
    # Mock call_api to return diagnostic
    mock_api_resp = MagicMock()
    mock_api_resp.status_code = 200
    mock_api_resp.json.return_value = {"response": "This is the AI Diagnostic Guide explaining the issue."}
    mock_ctx.call_api.return_value = mock_api_resp
    
    with patch("subprocess.run", return_value=mock_res), \
         patch("rich.prompt.Confirm.ask", return_value=True):
        cmd_debug(mock_ctx, ["python", "crash.py"])
        
        # Verify AI diagnostic was requested
        mock_ctx.call_api.assert_called_once()
        
        # Verify run_goal_loop was triggered
        mock_ctx.run_goal_loop.assert_called_once()
        args, kwargs = mock_ctx.run_goal_loop.call_args
        assert "Fix the crash/failure" in args[0]
        assert "non_existent" in args[0]

