import os
import requests
import json
import sys

def audit_file():
    target_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_monitor.py")
    with open(target_file, "r", encoding='utf-8') as f:
        code = f.read()

    prompt = f"""
Please perform a security, logic, and performance audit on the following system monitoring script:

```python
{code}
```

Identify any potential issues or edge cases.
"""

    payload = {
        "prompt": prompt,
        "agent_id": "reviewer"
    }

    print("Requesting audit from the reviewer agent...")
    try:
        response = requests.post("http://127.0.0.1:8000/generate", json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        print("\n--- Reviewer Agent Audit Report ---\n")
        print(result.get("response", result.get("content", "No content found in response.")))
        print("\n-----------------------------------")
    except Exception as e:
        print(f"Error calling reviewer agent: {e}")

if __name__ == "__main__":
    audit_file()
