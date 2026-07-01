from typing import Tuple

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

AGENT_MODELS: dict[str, Tuple[str, str]] = {
    "coordinator": ("qwen3.5:9b", "ollama"),
    "planner": ("qwen3.5:9b", "ollama"),
    "researcher": ("qwen3.5:9b", "ollama"),
    "executor": ("qwen2.5-coder:7b", "ollama"),
    "coder": ("qwen2.5-coder:7b", "ollama"),
    "tool-runner": ("llama3-groq-tool-use:8b", "ollama"),
    "reviewer": ("qwen3.5:9b", "ollama"),
    "debugger": ("ministral-3:8b", "ollama"),
}

def get_model(agent_id: str) -> Tuple[str, str]:
    return AGENT_MODELS.get(agent_id, ("qwen3.5:9b", "ollama"))

PREDICTIVE_TOPOLOGY: dict[str, str] = {
    "coordinator": "planner",
    "planner": "researcher",
    "researcher": "executor",
    "executor": "coder",
    "coder": "tool-runner",
    "tool-runner": "reviewer",
    "reviewer": "debugger",
    "debugger": "tool-runner",
}

def get_predicted_next_model(current_agent_id: str) -> str:
    next_agent = PREDICTIVE_TOPOLOGY.get(current_agent_id)
    if not next_agent:
        return ""
    model, backend = get_model(next_agent)
    return model if backend == "ollama" else ""
