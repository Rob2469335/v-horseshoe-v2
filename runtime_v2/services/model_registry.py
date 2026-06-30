from typing import Tuple

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

_AGENT_MODELS: dict[str, Tuple[str, str]] = {
    # Coordinator: deepseek-r1-tool-calling specializes in routing/tool decisions
    "coordinator": ("qwen3-coder:480b-cloud",             "ollama"),
    # Planner: qwen3.5:9b is the strongest reasoning model installed
    "planner":     ("qwen3-coder:480b-cloud",             "ollama"),
    # Executor: qwen2.5-coder:7b handles general task orchestration reliably without freezing
    "executor":    ("qwen2.5-coder:7b",                    "ollama"),
    # Coder: qwen3-coder:480b-cloud is an extremely powerful cloud-backed model for code
    "coder":       ("qwen3-coder:480b-cloud",             "ollama"),
    # Tool-runner: Groq Llama 3.3 is incredibly fast and great at tools
    "tool-runner": ("llama-3.3-70b-versatile",            "groq"),
    # Reviewer: OpenRouter Llama 3.3 free tier
    "reviewer":    ("meta-llama/llama-3.3-70b-instruct:free", "openrouter"),
    # Debugger: OpenRouter Llama 3.3 free tier
    "debugger":    ("meta-llama/llama-3.3-70b-instruct:free", "openrouter"),
}

def get_model(agent_id: str) -> Tuple[str, str]:
    return _AGENT_MODELS.get(agent_id, ("qwen3-coder:480b-cloud", "ollama"))


PREDICTIVE_TOPOLOGY: dict[str, str] = {
    "coordinator": "planner",
    "planner":     "coder",
    "coder":       "executor",
    "executor":    "reviewer",
    "reviewer":    "debugger",
    "debugger":    "coder",
}

def get_predicted_next_model(current_agent_id: str) -> str:
    next_agent = PREDICTIVE_TOPOLOGY.get(current_agent_id)
    if not next_agent:
        return ""
    model, backend = get_model(next_agent)
    return model if backend == "ollama" else ""

