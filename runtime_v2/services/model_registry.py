import json
import logging
from pathlib import Path
from typing import Tuple

log = logging.getLogger(__name__)

LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"

AGENT_MODELS: dict[str, Tuple[str, str]] = {
    # All agents use Qwen3.5-4B (MTP) on port 8080.
    "coordinator": ("qwen3.5-4b", "llama"),
    "planner": ("qwen3.5-4b", "llama"),
    "executor": ("qwen3.5-4b", "llama"),
    "tool-runner": ("qwen3.5-4b", "llama"),
    "reviewer": ("qwen3.5-4b", "llama"),
    "researcher": ("qwen3.5-4b", "llama"),
    "coder": ("qwen3.5-4b", "llama"),
    "debugger": ("qwen3.5-4b", "llama"),
    "tool-maker": ("qwen3.5-4b", "llama"),
    "code_analyzer": ("qwen3.5-4b", "llama"),
}


CONFIG_FILE = Path(__file__).parent.parent.parent / "config" / "agent_models.json"


def load_overrides():
    if CONFIG_FILE.exists():
        try:
            overrides = json.loads(CONFIG_FILE.read_text())
            for k, v in overrides.items():
                if isinstance(v, list) and len(v) == 2:
                    AGENT_MODELS[k] = (v[0], v[1])
        except Exception as e:
            log.warning(
                "Failed to load %s — using built-in agent->model defaults: %s",
                CONFIG_FILE,
                e,
            )


load_overrides()


def save_overrides():
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(AGENT_MODELS, indent=2))


def update_model_mapping(new_mapping: dict[str, str]):
    """Update AGENT_MODELS dynamically and persist to disk.
    new_mapping is expected to be {agent_id: model_name}.
    We assume backend is 'llama' for all auto-assigned local models.
    """
    for agent_id, model_name in new_mapping.items():
        if agent_id in AGENT_MODELS:
            AGENT_MODELS[agent_id] = (model_name, "llama")
    save_overrides()


def get_model(agent_id: str) -> Tuple[str, str]:
    return AGENT_MODELS.get(agent_id, ("qwen3.5-4b", "llama"))


PREDICTIVE_TOPOLOGY: dict[str, str] = {
    "coordinator": "planner",
    "planner": "researcher",
    "researcher": "executor",
    "executor": "coder",
    "coder": "tool-runner",
    "tool-runner": "reviewer",
    "reviewer": "debugger",
    "debugger": "tool-runner",
    "tool-maker": "tool-runner",
    "code_analyzer": "planner",
}


def get_predicted_next_model(current_agent_id: str) -> str:
    next_agent = PREDICTIVE_TOPOLOGY.get(current_agent_id)
    if not next_agent:
        return ""
    model, backend = get_model(next_agent)
    return model if backend in ("llama", "ollama") else ""


EXPERT_GATING_POLICY: dict[str, dict[str, int | str]] = {
    # High-precision tasks: full Top-2 MoE expert activation for complex synthesis
    "coordinator": {"policy": "top2_precision", "active_experts": 2},
    "planner": {"policy": "top2_precision", "active_experts": 2},
    "coder": {"policy": "top2_precision", "active_experts": 2},
    "debugger": {"policy": "top2_precision", "active_experts": 2},
    # Background/parsing tasks: Top-1 expert activation / fast decoding to halve memory bandwidth
    "researcher": {"policy": "top1_fast", "active_experts": 1},
    "tool-runner": {"policy": "top1_fast", "active_experts": 1},
    "tool-maker": {"policy": "top1_fast", "active_experts": 1},
    "code_analyzer": {"policy": "top1_fast", "active_experts": 1},
    "executor": {"policy": "top1_fast", "active_experts": 1},
    "reviewer": {"policy": "cloud_reasoning", "active_experts": 2},
}


def get_routing_policy(agent_id: str) -> dict[str, int | str]:
    return EXPERT_GATING_POLICY.get(
        agent_id, {"policy": "top2_precision", "active_experts": 2}
    )
