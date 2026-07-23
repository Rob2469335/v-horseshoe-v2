from __future__ import annotations
import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
from swarm_os.services.orchestrator import Orchestrator

@pytest.mark.asyncio
async def test_get_memory_context():
    orchestrator = Orchestrator()
    
    # Mock self.bridge._embed and self.bridge.vs.search
    orchestrator.bridge._embed = AsyncMock(return_value=[0.1] * 768)
    
    mock_hits = [
        {
            "payload": {
                "summary": "Ran search task successfully",
                "models": ["qwen2.5:7b-instruct"],
                "dominant_outcome": "success"
            }
        },
        {
            "payload": {
                "summary": "File editing failed",
                "models": ["qwen2.5:3b-instruct"],
                "dominant_outcome": "failure"
            }
        }
    ]
    orchestrator.bridge.vs.search = MagicMock(return_value=mock_hits)
    
    context = await orchestrator._get_memory_context("test query")
    assert "Relevant historical context" in context
    assert "Ran search task successfully" in context
    assert "File editing failed" in context

@pytest.mark.asyncio
async def test_generate_react_loop(tmp_path):
    orchestrator = Orchestrator()
    old_root = orchestrator.mcp.root
    orchestrator.mcp.root = tmp_path  # Redirect filesystem operations to sandbox
    try:
        # Mock memory context to return empty string to simplify test
        orchestrator._get_memory_context = AsyncMock(return_value="")
        
        # First LLM call yields a tool call; second yields a final answer
        tool_call_text = '<tool_call name="filesystem">{"operation": "write", "path": "temp_react.txt", "content": "hello from loop"}</tool_call>'
        final_response = "I have successfully written to temp_react.txt."
        
        orchestrator.ollama.generate = AsyncMock()
        orchestrator.ollama.generate.side_effect = [tool_call_text, final_response]
        
        result, model = await orchestrator.generate(
            model="qwen2.5:7b-instruct",
            prompt="Write hello to temp_react.txt"
        )
        
        assert result == final_response
        assert model == "qwen2.5:7b-instruct"
        
        # Verify file was written inside the sandbox
        written_file = tmp_path / "temp_react.txt"
        assert written_file.exists()
        assert written_file.read_text(encoding="utf-8") == "hello from loop"
        
        # Verify two calls were made to LLM
        assert orchestrator.ollama.generate.call_count == 2
    finally:
        orchestrator.mcp.root = old_root

@pytest.mark.asyncio
async def test_generate_react_loop_alternative_format(tmp_path):
    orchestrator = Orchestrator()
    old_root = orchestrator.mcp.root
    orchestrator.mcp.root = tmp_path
    try:
        orchestrator._get_memory_context = AsyncMock(return_value="")
        
        # Check alternate format <tool>...</tool>
        tool_call_text = '<tool>filesystem</tool> {"operation": "write", "path": "temp_react_alt.txt", "content": "hello alt"}'
        final_response = "I have written to temp_react_alt.txt."
        
        orchestrator.ollama.generate = AsyncMock()
        orchestrator.ollama.generate.side_effect = [tool_call_text, final_response]
        
        result, model = await orchestrator.generate(
            model="qwen2.5:7b-instruct",
            prompt="Write hello alt to temp_react_alt.txt"
        )
        
        assert result == final_response
        
        # Verify file was written inside the sandbox
        written_file = tmp_path / "temp_react_alt.txt"
        assert written_file.exists()
        assert written_file.read_text(encoding="utf-8") == "hello alt"
    finally:
        orchestrator.mcp.root = old_root

@pytest.mark.asyncio
async def test_stream_generate_react_loop(tmp_path):
    orchestrator = Orchestrator()
    old_root = orchestrator.mcp.root
    orchestrator.mcp.root = tmp_path
    try:
        orchestrator._get_memory_context = AsyncMock(return_value="")
        
        # Mock OllamaClient.stream_generate with two generators
        async def mock_stream_1(*args, **kwargs):
            yield '<tool_call name="filesystem">'
            yield '{"operation": "write", '
            yield '"path": "temp_stream.txt", '
            yield '"content": "hello from stream"}'
            yield '</tool_call>'
            
        async def mock_stream_2(*args, **kwargs):
            yield 'File was written successfully.'
            
        orchestrator.ollama.stream_generate = MagicMock()
        orchestrator.ollama.stream_generate.side_effect = [
            mock_stream_1(),
            mock_stream_2()
        ]
        
        chunks = []
        async for chunk, model, trace_id in orchestrator.stream_generate(
            model="qwen2.5:7b-instruct",
            prompt="Write hello stream"
        ):
            chunks.append(chunk)
            
        full_output = "".join(chunks)
        assert '<tool_call' in full_output
        assert '<tool_call' in full_output
        assert 'temp_stream.txt' in full_output
        assert 'File was written successfully.' in full_output
        assert 'File was written successfully.' in full_output
        
        # Verify file was written inside the sandbox
        written_file = tmp_path / "temp_stream.txt"
        assert written_file.exists()
        assert written_file.read_text(encoding="utf-8") == "hello from stream"
    finally:
        orchestrator.mcp.root = old_root

