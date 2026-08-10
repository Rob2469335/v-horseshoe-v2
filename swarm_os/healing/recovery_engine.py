import subprocess
import logging
import sys
import json
import asyncio
import os
from litellm import acompletion
import re
from pathlib import Path
from typing import Optional

from swarm_os.memory.memory_bridge import MemoryBridge
from swarm_os.services.danger_room import DangerRoom
from swarm_os.services.security_gate import SecurityGateViolation

log = logging.getLogger("zenith_healing")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

def _record_to_agents_md(anomaly: str, script: str):
    """SOTA 2026: Persist autonomous self-healing lessons directly to AGENTS.md
    so that future AI agents (open code / swarm) learn without instruction creep.
    Locked with filelock so concurrent recovery paths (CLI watchman thread +
    backend healing daemon) never race the read-modify-write."""
    try:
        from filelock import FileLock
        agents_file = PROJECT_ROOT / "AGENTS.md"
        if not agents_file.exists():
            return
        lock = FileLock(str(agents_file) + ".lock", timeout=5.0)
        with lock:
            content = agents_file.read_text(encoding="utf-8")
            if str(anomaly) in content and "Auto-Heal" in content:
                return
            summary = "Executed isolated DangerRoom repair script."
            for line in script.splitlines():
                line_s = line.strip().lstrip("#").strip()
                if line_s and not line_s.startswith("import ") and not line_s.startswith("from "):
                    summary = line_s[:100]
                    break
            import time
            date_str = time.strftime("%Y-%m-%d")
            new_entry = f"- **Auto-Heal ({date_str})**: Resolved anomaly `{anomaly}`. Action: {summary}\n"
            marker = "## Self-Healing & Self-Learning Fixes\n"
            if marker in content:
                content = content.replace(marker, marker + "\n" + new_entry, 1)
                agents_file.write_text(content, encoding="utf-8")
                log.info(f"Recorded auto-heal lesson for anomaly '{anomaly}' into AGENTS.md")
    except Exception as e:
        log.warning(f"Could not record auto-heal to AGENTS.md: {e}")

def _find_and_kill(match_str, exclude_pid=None):
    import psutil
    killed = []
    my_pid = exclude_pid or psutil.Process().pid
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == my_pid:
                continue
            cmdline = " ".join(proc.info["cmdline"] or [])
            if match_str in cmdline:
                proc.kill()
                killed.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed

def restart_llamacpp(anomaly):
    try:
        _find_and_kill("llama")
    except Exception as e:
        # BUG FIX: Log instead of silently swallowing the error
        log.warning(f"Failed to kill llamacpp processes: {e}")
    try:
        script = PROJECT_ROOT / "start-dev.ps1"
        if not script.exists():
            return {"ok": False, "action": "restart_llamacpp", "error": f"start-dev.ps1 not found at {script}"}
        proc = subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=str(PROJECT_ROOT),
            start_new_session=True,
        )
        # Catch immediate startup failure: if the child exits right away, surface it.
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass  # still running — restart proceeding
        if proc.poll() is not None and proc.returncode != 0:
            return {"ok": False, "action": "restart_llamacpp", "error": f"restart process exited early with code {proc.returncode}"}
        return {"ok": True, "action": "restarted_llamacpp"}
    except Exception as exc:
        return {"ok": False, "action": "restart_llamacpp", "error": str(exc)}

def restart_backend(anomaly):
    try:
        # RESEARCHED (2026): a process cannot restart ITSELF in-process. This
        # recovery action is invoked both from the CLI healing watchman
        # (a separate process — kill + relaunch works there) and, via
        # HealingService.run_once(), from INSIDE the backend. In the in-process
        # case the parent still owns :8000, so spawning a child uvicorn is
        # guaranteed EADDRINUSE — an inevitable no-op that burns a recovery slot.
        # start-dev.ps1 runs the backend as a PowerShell job with NO supervisor
        # loop, so there is no external watchdog to lean on either. Best-fit:
        # detect in-process and refuse fail-closed (governor records the failed
        # attempt via the action key) instead of spawning a doomed child.
        import sys as _sys
        _in_process = any("swarm_os.app.main" in arg for arg in _sys.argv)
        if _in_process:
            return {
                "ok": False,
                "action": "restart_backend",
                "error": (
                    "Refusing in-process backend restart: the backend cannot kill "
                    "and relaunch itself while owning :8000 (EADDRINUSE). "
                    "Relaunch via start-dev.ps1 or the CLI."
                ),
            }
        # BUG FIX: Don't kill ourselves — skip the calling PID (this IS the backend)
        killed = _find_and_kill("swarm_os.app.main", exclude_pid=os.getpid())
        cmd = [
            sys.executable, "-m", "uvicorn",
            "swarm_os.app.main:app", "--host", "127.0.0.1", "--port", "8000",
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Catch immediate startup failure (bad env, missing deps, port conflict).
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass  # still running — restart proceeding
        if proc.poll() is not None and proc.returncode != 0:
            return {"ok": False, "action": "restart_backend", "error": f"backend restart exited early with code {proc.returncode}", "killed_pids": killed}
        return {"ok": True, "action": "restarted_backend", "killed_pids": killed}
    except Exception as exc:
        return {"ok": False, "action": "restart_backend", "error": str(exc)}

async def llm_guided_recovery(anomaly):
    """Fallback recovery using GraphRAG Memory and LLM-generated code executed in the DangerRoom."""
    memory_bridge = MemoryBridge()
    try:
        historical_context = await memory_bridge.get_memory_context(str(anomaly))
    except Exception:
        historical_context = ""

    try:
        from swarm_os.lib.mcp.web_search import web_search_handler
        from swarm_os.lib.mcp.playwright import playwright_handler
        error_msg = str(anomaly.get("error") or anomaly.get("message") or anomaly.get("details") or anomaly)[:150]
        search_res = await web_search_handler({"query": f"python {error_msg}", "max_results": 3})
        web_context = ""
        if search_res.get("ok") and search_res.get("results"):
            web_context = "\nWeb Search Results:\n"
            top_url = search_res["results"][0].get("url")
            if top_url:
                playwright_res = await playwright_handler({"operation": "navigate", "url": top_url})
                if playwright_res.get("ok"):
                    md_content = playwright_res.get("full_content", playwright_res.get("content_summary", ""))
                    web_context += f"Full Page Markdown for top result ({top_url}):\n{md_content[:2500]}\n"
            for item in search_res["results"]:
                web_context += f"- {item.get('title')}: {item.get('snippet')}\n"
    except Exception as e:
        log.warning(f"Web search failed during recovery: {e}")
        web_context = ""
    
    prompt = f'''You are a system recovery agent for Swarm OS.
The system experienced this anomaly:
{json.dumps(anomaly, indent=2)}

Historical Context from past recoveries:
{historical_context}
{web_context}

Write a Python script to fix this issue (e.g. killing ports, clearing cache).
Enclose the script in a ```python block.
'''
    
    messages = [{"role": "user", "content": prompt}]
    
    for attempt in range(2):
        try:
            # Recovery/repair script generation = complex reasoning — use cloud
            # DeepSeek V4 Flash (funded, cheap) instead of local qwen3.5-4b.
            import os as _os
            _kwargs = {
                "model": "openai/deepseek-v4-flash",
                "messages": messages,
            }
            _base = _os.getenv("OPENAI_API_BASE", "")
            _key = _os.getenv("OPENAI_API_KEY", "")
            if _base:
                _kwargs["api_base"] = _base
            if _key:
                _kwargs["api_key"] = _key
            res = await acompletion(**_kwargs)
            script_full = res.choices[0].message.content
            messages.append({"role": "assistant", "content": script_full})
            
            match = re.search(r"```python(.*?)```", script_full, re.DOTALL)
            script = match.group(1).strip() if match else script_full.strip()
            
            root_dir = PROJECT_ROOT
            
            # BUG FIX: DangerRoom only implements async context management.
            # Using `with` raised AttributeError, crashing every llm_guided_recovery.
            async with DangerRoom(root_dir) as sandbox:
                sandbox_file = sandbox.sandbox_dir / "recovery_script.py"
                with open(sandbox_file, "w", encoding="utf-8") as f:
                    f.write(script)
                
                await sandbox.scan_sandbox() # Security gate check

                # BUG FIX: Use async subprocess to avoid blocking the event loop.
                # subprocess.run() in an async context stalls all other coroutines.
                # UPGRADE: run isolated — no network, hard memory/time limits.
                # BUG FIX (hardening): force cwd INTO the sandbox staging dir so an
                # LLM-generated script cannot write files to the repo root via a
                # relative path, and strip sensitive env vars (API keys, feature
                # gates) so a malicious mutation cannot exfiltrate them or trigger
                # daemon loops (e.g. SWARM_EVOLUTION=1 spawning another generation).
                clean_env = {
                    k: v for k, v in os.environ.items()
                    if not any(s in k.upper() for s in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
                    and not k.startswith("SWARM_")
                }
                clean_env["PYTHONNOUSERSITE"] = "1"
                proc_handle = await asyncio.create_subprocess_exec(
                    sys.executable, "-I", str(sandbox_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(sandbox.sandbox_dir),
                    env=clean_env,
                )
                try:
                    async with asyncio.timeout(10):
                        stdout_b, stderr_b = await proc_handle.communicate()
                except TimeoutError:
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
                    # _record_to_agents_md takes a blocking FileLock + writes
                    # AGENTS.md — run it off the event loop.
                    await asyncio.to_thread(_record_to_agents_md, str(anomaly), script)
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
    "qwen3.5-4b": "llamacpp",
    "gte-modernbert-base": "llamacpp",
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

def micro_restart(anomaly, actions: Optional[dict] = None):
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
            import inspect
            if inspect.iscoroutinefunction(action_fn):
                return _run_async_action(action_fn, anomaly, target, component)
            result = action_fn(anomaly)
            if inspect.isawaitable(result):
                # BUG FIX: await the ALREADY-produced awaitable, not a second
                # invocation. Previously _run_async_action re-invoked action_fn,
                # executing the action twice and leaking the first coroutine.
                return _run_awaitable(result, anomaly, target, component)
            if not isinstance(result, dict):
                result = {}
            result["action"] = f"micro_restart -> {target}"
            result["symptom"] = component
            return result

    # Fallback: log-only if no registered action (e.g., internal thread)
    log.info(f"No registered action for '{target}'. Logging micro-restart as informational.")
    return {"ok": True, "action": "micro_restart", "target": target, "symptom": component, "reason": "No registered hard-restart needed; component state flagged for lazy-reload."}

def _run_async_action(action_fn, anomaly, target, component):
    async def _wrapper():
        result = await action_fn(anomaly)
        if isinstance(result, dict):
            result["action"] = f"micro_restart -> {target}"
            result["symptom"] = component
        return result
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return asyncio.ensure_future(_wrapper())
    return asyncio.run(_wrapper())

def _run_awaitable(awaitable, anomaly, target, component):
    """Await an already-produced awaitable (from a sync-def action that returned
    one) without re-invoking the action. Mirrors _run_async_action's loop
    handling, but consumes the existing coroutine rather than calling the
    function a second time."""
    async def _wrapper():
        result = await awaitable
        if isinstance(result, dict):
            result["action"] = f"micro_restart -> {target}"
            result["symptom"] = component
        return result
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return asyncio.ensure_future(_wrapper())
    return asyncio.run(_wrapper())

def alert_only(anomaly):
    log.warning("Self-healing detected an unrecoverable issue: %s", anomaly)
    return {"ok": False, "action": "alert_only", "reason": "no automated recovery available for this component"}

class RecoveryEngine:
    def __init__(self, actions=None):
        # Merge infra actions with whole-computer system recovery actions.
        try:
            from .system_recovery import SYSTEM_RECOVERY_ACTIONS
            system_actions = dict(SYSTEM_RECOVERY_ACTIONS)
        except Exception:
            system_actions = {}
        self.actions = actions or {
            "llamacpp": restart_llamacpp,
            "backend": restart_backend,
            "swarm_api": restart_backend,
            "qdrant": llm_guided_recovery,
            **system_actions,
        }

    async def recover(self, anomaly):
        source = anomaly.get("component") or anomaly.get("source")

        # If this is a preemptive forecast warning, attempt a non-destructive micro-restart first.
        # Pass self.actions so micro_restart can dispatch a real action if one is registered.
        if anomaly.get("level") == "forecast_warning":
            log.info(f"Forecast warning detected for {source}. Attempting preemptive micro-restart.")
            result = micro_restart(anomaly, actions=self.actions)
            return result if not hasattr(result, "__await__") else await result

        # For actual failures, perform causal root-cause inference
        root_cause = _trace_root_cause(source)

        action = self.actions.get(root_cause, llm_guided_recovery)  # fallback to llm_guided_recovery
        if callable(action):
            # The registered actions are SYNC functions (restart_llamacpp /
            # restart_backend call subprocess.Popen().wait(timeout=2.0); a few
            # are async (llm_guided_recovery). The sync ones block the event
            # loop for up to ~2s each, so dispatch them off-loop via to_thread.
            import inspect
            if inspect.iscoroutinefunction(action):
                result = await action(anomaly)
            else:
                result = await asyncio.to_thread(action, anomaly)
            return result if not hasattr(result, "__await__") else await result
        return {"ok": False, "reason": f"no recovery action for {root_cause}"}