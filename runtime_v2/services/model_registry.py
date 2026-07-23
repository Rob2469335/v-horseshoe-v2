import json
from pathlib import Path
from typing import Tuple

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

AGENT_MODELS: dict[str, Tuple[str, str]] = {
    # System Agents
    "coordinator": ("qwen3-4b-tuned", "ollama"),
    "planner": ("qwen-tuned", "ollama"),
    "researcher": ("qwen-tuned", "ollama"),
    "executor": ("qwen3-4b-tuned", "ollama"),
    "coder": ("qwen-tuned", "ollama"),
    "tool-runner": ("qwen3-4b-tuned", "ollama"),
    "reviewer": ("qwen-tuned", "ollama"),  # External 30B reviewer: intentionally stronger than qwen-tuned to catch what the author missed
    "debugger": ("qwen-tuned", "ollama"),
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
    CONFIG_FILE.write_text(json.dumps(AGENT_MODELS, indent=2))

def update_model_mapping(new_mapping: dict[str, str]):
    """Update AGENT_MODELS dynamically and persist to disk.
    new_mapping is expected to be {agent_id: model_name}.
    We assume backend is 'ollama' for all auto-assigned local models.
    """
    for agent_id, model_name in new_mapping.items():
        if agent_id in AGENT_MODELS:
            AGENT_MODELS[agent_id] = (model_name, "ollama")
    save_overrides()

def get_model(agent_id: str) -> Tuple[str, str]:
    return AGENT_MODELS.get(agent_id, ("qwen-tuned", "ollama"))




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


