import re

with open('runtime_v2/services/model_registry.py', 'r', encoding='utf-8') as f:
    code = f.read()

topology = '''
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
'''

if 'PREDICTIVE_TOPOLOGY' not in code:
    code += '\n' + topology + '\n'

with open('runtime_v2/services/model_registry.py', 'w', encoding='utf-8') as f:
    f.write(code)
