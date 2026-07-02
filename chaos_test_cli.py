import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from organism_console.command_registry import registry, CommandContext

class DummyState:
    def __init__(self):
        self.command_history = []
        self.last_error = None
    def save(self): pass

def test_goal_intent_multiline():
    print("Testing /goal intent with multiline prompt...")
    def mock_call_api(endpoint, method, payload=None, stream=False):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "response": '```json\n{"command": "/goal fix this\nand that", "confidence": 0.9}\n```'
        }
        return response
        
    goal_loop_mock = MagicMock()
    ctx = CommandContext(
        state=DummyState(),
        console=MagicMock(),
        call_api=mock_call_api,
        run_prompt=MagicMock(),
        get_system_stats=MagicMock(),
        installed_models=["qwen2.5-coder:7b"],
        run_goal_loop=goal_loop_mock
    )
    
    multi_line_prompt = "Fix the caching logic\nIt throws NullReferenceException"
    registry.handle_line(multi_line_prompt, ctx)
    goal_loop_mock.assert_called_once_with(multi_line_prompt)
    print("✓ /goal intent preserves the original multiline prompt!")

if __name__ == "__main__":
    test_goal_intent_multiline()
