import json
from pathlib import Path
from typing import Tuple

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

AGENT_MODELS: dict[str, Tuple[str, str]] = {
    "coordinator": ("llama3-groq-tool-use:8b", "ollama"),
    "planner": ("llama3-groq-tool-use:8b", "ollama"),
    "researcher": ("llama3-groq-tool-use:8b", "ollama"),
    "executor": ("qwen2.5-coder:7b", "ollama"),
    "coder": ("qwen2.5-coder:7b", "ollama"),
    "tool-runner": ("llama3-groq-tool-use:8b", "ollama"),
    "reviewer": ("llama3-groq-tool-use:8b", "ollama"),
    "debugger": ("MFDoom/deepseek-r1-tool-calling:8b", "ollama"),
}

CONFIG_FILE = Path(__file__).parent.parent.parent / "config" / "agent_models.json"

def load_overrides():
    if CONFIG_FILE.exists():
        try:
            overrides = json.loads(CONFIG_FILE.read_text())
            for k, v in overrides.items():
                if isinstance(v, list) and len(v) == 2:
                    AGENT_MODELS[k] = (v[0], v[1])
        except Exception:
            pass

load_overrides()

def save_overrides():
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(AGENT_MODELS))

def get_model(agent_id: str) -> Tuple[str, str]:
    return AGENT_MODELS.get(agent_id, ("llama3-groq-tool-use:8b", "ollama"))

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
