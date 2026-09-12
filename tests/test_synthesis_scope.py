import pytest
import json
import os
from unittest.mock import patch

from runtime_v2.api.agent_service_v2 import AgentServiceV2

@pytest.mark.asyncio
async def test_forced_synthesis_subsets_read_list_to_context():
    service = AgentServiceV2()
    
    captured_forced_prompt = ""
    turn = 0
    
    async def mock_call_llm(model, prompt_messages, agent_id, allowed_tools=None):
        nonlocal captured_forced_prompt, turn
        if allowed_tools == ["final"]:
            captured_forced_prompt = json.dumps(prompt_messages)
            return {"action": "final", "response": "Synthesis complete."}
            
        file_path = f"file_{turn}.py"
        turn += 1
        return {"action": "filesystem", "operation": "read", "path": file_path}
        
    service._call_llm = mock_call_llm
    
    async def mock_run_tool(*args, **kwargs):
        payload = kwargs.get('payload') or (args[2] if len(args) > 2 else {})
        return {"ok": True, "content": f"Contents of {payload.get('path', 'unknown')}"}
    
    # Run the stream until max turns
    with patch("runtime_v2.services.memory_core.get_relevant_memories", return_value=""):
        with patch("runtime_v2.services.memory_core.get_embedding", return_value=[0.0] * 384):
            with patch("runtime_v2.services._llm_client.get_litellm_model", return_value="dummy"):
                with patch.dict(os.environ, {"SWARM_DEEP_MAX_TURNS": "8"}):
                    with patch("runtime_v2.services.tool_executor.run", new=mock_run_tool):
                        _ = [chunk async for chunk in service._step_agent_stream_inner(
                            agent_id="code_analyzer", 
                            prompt="analyze codebase for bugs", # "analyze codebase" triggers deep budget
                        )]
    # Assert that early files (which were previously trimmed out) are now included
    assert "AGENTS.md" in captured_forced_prompt
    assert "agent_service_v2.py" in captured_forced_prompt
    assert "stream_runner.py" in captured_forced_prompt
    
    # Assert that late files are also included
    assert "routes.py" in captured_forced_prompt
    assert "orchestrator.py" in captured_forced_prompt
    assert "watch_loop.py" in captured_forced_prompt
    

