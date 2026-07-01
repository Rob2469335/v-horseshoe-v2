from typing import Tuple

OLLAMA_URL = 'http://127.0.0.1:11434/api/chat'

_AGENT_MODELS: dict[str, Tuple[str, str]] = {
    'coordinator': ('qwen3-coder:480b-cloud',             'ollama'),
    'planner':     ('qwen3-coder:480b-cloud',             'ollama'),
    'executor':    ('qwen2.5-coder:7b',                    'ollama'),
    'coder':       ('qwen3-coder:480b-cloud',             'ollama'),
    'tool-runner': ('llama-3.3-70b-versatile',            'groq'),
    'reviewer':    ('meta-llama/llama-3.3-70b-instruct:free', 'openrouter'),
    'debugger':    ('meta-llama/llama-3.3-70b-instruct:free', 'openrouter'),
}

def get_model(agent_id: str) -> Tuple[str, str]:
    return _AGENT_MODELS.get(agent_id, ('qwen3-coder:480b-cloud', 'ollama'))


PREDICTIVE_TOPOLOGY: dict[str, str] = {
    'coordinator': 'planner',
    'planner':     'executor',
    'executor':    'coder',
    'coder':       'tool-runner',
    'tool-runner': 'reviewer',
    'reviewer':    'coder',
    'debugger':    'tool-runner',
}

def get_predicted_next_model(current_agent_id: str) -> str:
    next_agent = PREDICTIVE_TOPOLOGY.get(current_agent_id)
    if not next_agent:
        return ''
    model, backend = get_model(next_agent)
    return model if backend == 'ollama' else ''

