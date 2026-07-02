import asyncio
import os
import sys
import swarm_os.bootstrap

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

# 1. Create a broken file
broken_code = """
import urllib.request
import json
# This API endpoint requires a special User-Agent and JSON parsing, 
# but we are doing it completely wrong and it crashes.
def test_fetch_weather():
    url = "https://api.weather.gov/points/39.7456,-97.0892"
    # This fails because we are using an empty User-Agent parameter
    req = urllib.request.Request(url)
    req.add_header('User-Agent', '')
    resp = urllib.request.urlopen(req)
    assert resp.getcode() == 200
"""
with open("tests/test_weather.py", "w") as f:
    f.write(broken_code.strip())
print("Created 'tests/test_weather.py' with intentional 403 Forbidden flaw.")

# 2. Trigger the goal loop directly using the CLI's logic
from organism_console.cli import CLIContext
from organism_console.api_client import call_api
from organism_console.ui.banner import get_system_stats
from organism_console.ui.live_stream import stream_prompt_with_retry
from organism_console.command_registry import CommandContext
from organism_console.loops.autonomous import run_autonomous_goal_loop
from rich.console import Console

state = CLIContext()
console = Console()
ctx = CommandContext(
    state=state,
    console=console,
    call_api=call_api,
    run_prompt=lambda p: stream_prompt_with_retry(state, state.active_agent, p, state.history),
    get_system_stats=get_system_stats,
    installed_models=["qwen2.5-coder:7b"]
)
goal = "The script tests/test_weather.py is failing with HTTP 403 Forbidden because it is missing a User-Agent header. Please use web_search to research how to add a User-Agent with python urllib.request and then use filesystem to fix the code."
print(f"\n--- TRIGGERING AUTONOMOUS LOOP ---")
print(f"Goal: {goal}")
try:
    run_autonomous_goal_loop(goal, ctx)
except Exception as e:
    print(f"Exception during autonomous loop: {e}")

# 3. Verify
print("\n--- FINAL VERIFICATION ---")
if os.path.exists("tests/test_weather.py"):
    with open("tests/test_weather.py", "r") as f:
        final_code = f.read()
    if "headers=" in final_code or "add_header" in final_code or "requests.get" in final_code:
        print("[SUCCESS] The agent successfully researched and injected the missing header!")
    else:
        print("[FAILED] The agent failed to apply the fix.")
        print(f"Final Code:\n{final_code}")
else:
    print("[FAILED] File was deleted.")
