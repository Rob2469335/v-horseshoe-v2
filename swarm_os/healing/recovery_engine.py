import subprocess
import logging
import sys

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
    except Exception:
        pass
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


def alert_only(anomaly):
    log.warning("Self-healing detected an unrecoverable issue: %s", anomaly)
    return {"ok": False, "action": "alert_only", "reason": "no automated recovery available for this component"}


class RecoveryEngine:
    def __init__(self, actions=None):
        self.actions = actions or {
            "ollama": restart_ollama,
            "backend": restart_backend,
            "swarm_api": restart_backend,
            "qdrant": alert_only,
        }

    async def recover(self, anomaly):
        source = anomaly.get("component") or anomaly.get("source")
        action = self.actions.get(source, alert_only)
        if callable(action):
            result = action(anomaly)
            return result if not hasattr(result, "__await__") else await result
        return {"ok": False, "reason": f"no recovery action for {source}"}