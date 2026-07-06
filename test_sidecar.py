import asyncio
import logging
import os
import sys

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test_sidecar")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime_v2.services.stream_runner import get_tool_decision
from runtime_v2.services.model_registry import load_overrides

async def main():
    load_overrides()

    # Mock the internal litellm call just for the initial broken response
    # We will monkeypatch _complete_for_tool_decision to return garbage
    import runtime_v2.services.stream_runner as stream_runner
    
    class MockMessage:
        content = "Here is my thought. I think I should run a tool. ```json\n{ \"action\": \"search_web\", \"query\": \"what is the weather\",\n```"
    class MockChoice:
        message = MockMessage()
    class MockResponse:
        choices = [MockChoice()]

    async def mock_complete(*args, **kwargs):
        log.info("Mocking primary model response with broken JSON...")
        return MockResponse()

    stream_runner._complete_for_tool_decision = mock_complete

    messages = [{"role": "user", "content": "Hello, check the weather."}]
    agent_id = "coordinator"
    model = "llama3-groq-tool-use:8b"

    log.info("Triggering get_tool_decision...")
    result = await get_tool_decision(model, messages, agent_id)
    
    log.info("Final Decision Result: %s", result)

if __name__ == "__main__":
    asyncio.run(main())
