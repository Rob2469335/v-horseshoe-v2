from unittest.mock import MagicMock, patch
from swarm_os.brain import _build_system_prompt, make_swarm_brain


class MockCognition:
    def __init__(self):
        self.decomposition_bias = 0.5
        self.max_subtasks = 3
        self.self_critique_bias = 0.5
        self.reflection_depth = 0.5
        self.verification_bias = 0.5
        self.hallucination_sensitivity = 0.5
        self.retry_aggression = 0.5
        self.summarization_bias = 0.5
        self.parallel_tool_calls = 0.5


class MockGenome:
    def __init__(self):
        self.cognition = MockCognition()
        self.reasoning_depth = 0.5
        self.verbosity = 0.5
        self.context_budget = 0.5
        self.timeout_budget = 300.0
        self.model = "test-model"

    def active_tools(self):
        return ["filesystem"]


def test_build_system_prompt_cognitive_biases():
    genome = MockGenome()

    # Test high reasoning depth
    genome.reasoning_depth = 0.8
    prompt = _build_system_prompt(genome, "general")
    assert "Think step by step" in prompt

    # Test low reasoning depth
    genome.reasoning_depth = 0.2
    prompt = _build_system_prompt(genome, "general")
    assert "Answer directly and concisely" in prompt

    # Test hallucination sensitivity
    genome.cognition.hallucination_sensitivity = 0.9
    prompt = _build_system_prompt(genome, "general")
    assert "Do not invent facts" in prompt

    # Test coding domain
    prompt = _build_system_prompt(genome, "coding")
    assert "You are a precise software engineer" in prompt


@patch("swarm_os.brain.time.sleep")
def test_brain_http_exponential_backoff(mock_sleep):
    genome = MockGenome()
    brain_fn = make_swarm_brain(genome)

    with patch("swarm_os.brain.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        # Simulate 429 Too Many Requests twice, then 200 OK
        resp_429 = MagicMock()
        resp_429.status_code = 429

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.text = "success"

        mock_client.post.side_effect = [resp_429, resp_429, resp_200]

        brain_fn({"task": "test task"})

        # Should have called post 3 times
        assert mock_client.post.call_count == 3

        # Should have slept twice due to the 429s
        assert mock_sleep.call_count == 2
