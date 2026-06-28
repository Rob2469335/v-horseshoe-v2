"""
Model registry - final assignments from June 2026 benchmark.

executor/tool-runner: llama3-groq-tool-use:8b - only model with TaskSuccess=1 on execution
coder:               qwen3:8b-q4_K_M          - TaskSuccess=1 + highest TeacherScore on code
coordinator/planner/reviewer/debugger: gemma4:e4b - best reasoning, native tool calling
"""
from typing import Tuple

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

_AGENT_MODELS: dict[str, Tuple[str, str]] = {
    "coordinator": ("gemma4:e4b",               "ollama"),
    "planner":     ("gemma4:e4b",               "ollama"),
    "executor":    ("llama3-groq-tool-use:8b",  "ollama"),
    "coder":       ("qwen3:8b-q4_K_M",          "ollama"),
    "tool-runner": ("llama3-groq-tool-use:8b",  "ollama"),
    "debugger":    ("gemma4:e4b",               "ollama"),
    "reviewer":    ("gemma4:e4b",               "ollama"),
}

def get_model(agent_id: str) -> Tuple[str, str]:
    return _AGENT_MODELS.get(agent_id, ("gemma4:e4b", "ollama"))
