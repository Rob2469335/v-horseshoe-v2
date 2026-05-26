import json

def log_combination(tool_a: str, tool_b: str):
    """
    Logs unique tool combinations to detect emergent behavior.
    """
    entry = {"combination": [tool_a, tool_b]}
    with open("swarm_behavior.log", "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"Emergent behavior detected: {tool_a} linked with {tool_b}")
