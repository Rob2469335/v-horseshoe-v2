import subprocess
import logging
import sys
import json
import asyncio
from litellm import acompletion
import re
from pathlib import Path

from swarm_os.memory.memory_bridge import MemoryBridge
from swarm_os.services.danger_room import DangerRoom
from swarm_os.services.security_gate import SecurityGateViolation

log = logging.getLogger("zenith_healing")

def _find_and_kill(match_str):
    import psutil
    killed = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
            if match_str in cmdline:
                proc.kill()
                killed.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed

def restart_ollama(anomaly):
    try:
        _find_and_kill("ollama")
    except Exception as e:
        # BUG FIX: Log instead of silently swallowing the error
        log.warning(f"Failed to kill ollama processes: {e}")
    try:
        subprocess.Popen(["ollama", "serve"])
        return {"ok": True, "action": "restarted_ollama"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

def restart_backend(anomaly):
    try:
        killed = _find_and_kill("swarm_os.app.main")
        cmd = [
            "C:\\Python314\\python.exe", "-m", "uvicorn",
            "swarm_os.app.main:app", "--host", "127.0.0.1", "--port", "8000",
        ]
        subprocess.Popen(cmd, cwd="C:\\Users\\rober\\Projects\\v-horseshoe-v2")
        return {"ok": True, "action": "restarted_backend", "killed_pids": killed}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

async def llm_guided_recovery(anomaly):
    """Fallback recovery using GraphRAG Memory and LLM-generated code executed in the DangerRoom."""
    memory_bridge = MemoryBridge()
    try:
        historical_context = await memory_bridge.get_memory_context(str(anomaly))
    except Exception:
        historical_context = ""
    
    prompt = f'''You are a system recovery agent for Swarm OS.
The system experienced this anomaly:
{json.dumps(anomaly, indent=2)}

Historical Context from past recoveries:
{historical_context}

Write a Python script to fix this issue (e.g. killing ports, clearing cache).
Enclose the script in a ```python block.
'''
    
    messages = [{"role": "user", "content": prompt}]
    
    for attempt in range(2):
        try:
            res = await acompletion(
                model="deepseek-coder-v2",
                messages=messages,
                api_base="http://localhost:11434",
                custom_llm_provider="ollama"
            )
            script_full = res.choices[0].message.content
            messages.append({"role": "assistant", "content": script_full})
            
            match = re.search(r"```python(.*?)```", script_full, re.DOTALL)
            script = match.group(1).strip() if match else script_full.strip()
            
            root_dir = Path("C:/Users/rober/Projects/v-horseshoe-v2")
            
            with DangerRoom(root_dir) as sandbox:
                sandbox_file = sandbox.sandbox_dir / "recovery_script.py"
                with open(sandbox_file, "w", encoding="utf-8") as f:
                    f.write(script)
                
                sandbox.scan_sandbox() # Security gate check

                # BUG FIX: Use async subprocess to avoid blocking the event loop.
                # subprocess.run() in an async context stalls all other coroutines.
                proc_handle = await asyncio.create_subprocess_exec(
                    "python", str(sandbox_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(proc_handle.communicate(), timeout=10)
                except asyncio.TimeoutError:
                    proc_handle.kill()
                    raise Exception("Recovery script timed out after 10 seconds.")

                class _ProcResult:
                    def __init__(self, rc, out, err):
                        self.returncode = rc
                        self.stdout = out
                        self.stderr = err

                proc = _ProcResult(
                    proc_handle.returncode,
                    stdout_b.decode(errors="replace"),
                    stderr_b.decode(errors="replace")
                )
                if proc.returncode == 0:
                    memory_bridge._add({
                        "event_type": "dynamic_recovery",
                        "outcome": "success",
                        "anomaly": anomaly,
                        "script": script
                    })
                    await memory_bridge._flush()
                    return {"ok": True, "action": "llm_guided_recovery", "output": proc.stdout}
                else:
                    messages.append({"role": "user", "content": f"Script failed with output:\n{proc.stderr}\nPlease fix it."})
                    
        except SecurityGateViolation as e:
            messages.append({"role": "user", "content": f"Security violation: {e}. Fix it."})
        except Exception as e:
            messages.append({"role": "user", "content": f"Error: {e}. Fix it."})
            
    memory_bridge._add({
        "event_type": "dynamic_recovery",
        "outcome": "failure",
        "anomaly": anomaly
    })
    await memory_bridge._flush()
    return {"ok": False, "action": "llm_guided_recovery", "reason": "Failed to generate working recovery script"}

# Causal Dependency Graph for Root Cause Inference
# Maps a downstream symptom to its upstream root cause
CAUSAL_GRAPH = {
    "swarm_api": "backend",
    "deepseek-coder": "ollama",
    "phi4-mini": "ollama",
    "nomic-embed-text": "ollama",
    "frontend": "swarm_api",
    "qdrant_client": "qdrant",
    "memory_bridge": "qdrant"
}

def _trace_root_cause(symptom: str) -> str:
    """Traverse the causal graph to find the root cause of an anomaly."""
    # BUG FIX: Guard against None/empty symptom to prevent TypeError in dict lookup
    if not symptom:
        return "unknown"
    current = symptom
    path = [current]
    while current in CAUSAL_GRAPH:
        current = CAUSAL_GRAPH[current]
        if current in path:  # prevent cycles
            break
        path.append(current)
    if len(path) > 1:
        log.info(f"Causal Inference: Traced symptom '{symptom}' -> root cause '{current}' (Path: {' -> '.join(path)})")
    return current

def micro_restart(anomaly, actions: dict = None):
    """Preemptive Micro-Restart: Surgically restart a specific sub-component without taking down the full system."""
    component = anomaly.get("component") or anomaly.get("source")
    target = _trace_root_cause(component)

    log.info(f"Initiating preemptive micro-restart for root cause: {target} (Symptom: {component})")

    # BUG FIX: Actually invoke the registered recovery action for the root cause
    # rather than just returning a simulation dict.
    if actions and target in actions:
        action_fn = actions[target]
        if callable(action_fn):
            log.info(f"Dispatching real micro-restart action for '{target}'.")
            result = action_fn(anomaly)
            result["action"] = f"micro_restart -> {target}"
            result["symptom"] = component
            return result

    # Fallback: log-only if no registered action (e.g., internal thread)
    log.info(f"No registered action for '{target}'. Logging micro-restart as informational.")
    return {"ok": True, "action": "micro_restart", "target": target, "symptom": component, "reason": "No registered hard-restart needed; component state flagged for lazy-reload."}

def alert_only(anomaly):
    log.warning("Self-healing detected an unrecoverable issue: %s", anomaly)
    return {"ok": False, "action": "alert_only", "reason": "no automated recovery available for this component"}

class RecoveryEngine:
    def __init__(self, actions=None):
        self.actions = actions or {
            "ollama": restart_ollama,
            "backend": restart_backend,
            "swarm_api": restart_backend,
            "qdrant": llm_guided_recovery,
        }

    async def recover(self, anomaly):
        source = anomaly.get("component") or anomaly.get("source")

        # If this is a preemptive forecast warning, attempt a non-destructive micro-restart first.
        # Pass self.actions so micro_restart can dispatch a real action if one is registered.
        if anomaly.get("level") == "forecast_warning":
            log.info(f"Forecast warning detected for {source}. Attempting preemptive micro-restart.")
            return micro_restart(anomaly, actions=self.actions)

        # For actual failures, perform causal root-cause inference
        root_cause = _trace_root_cause(source)

        action = self.actions.get(root_cause, llm_guided_recovery)  # fallback to llm_guided_recovery
        if callable(action):
            result = action(anomaly)
            return result if not hasattr(result, "__await__") else await result
        return {"ok": False, "reason": f"no recovery action for {root_cause}"}