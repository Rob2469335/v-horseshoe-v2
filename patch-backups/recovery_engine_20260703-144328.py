import subprocess
import logging

log = logging.getLogger("zenith_healing")


def restart_ollama(anomaly):
    try:
        subprocess.run(["ollama", "stop"], timeout=10, capture_output=True)
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return {"ok": True, "action": "restarted_ollama"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def restart_backend_nssm(anomaly):
    try:
        result = subprocess.run(["nssm", "restart", "ZenithBackend"], capture_output=True, text=True, timeout=15)
        return {"ok": result.returncode == 0, "action": "restarted_backend", "detail": result.stdout}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def alert_only(anomaly):
    log.warning("Self-healing detected an unrecoverable issue: %s", anomaly)
    return {"ok": False, "action": "alert_only", "reason": "no automated recovery available for this component"}


class RecoveryEngine:
    def __init__(self, actions=None):
        self.actions = actions or {
            "ollama": restart_ollama,
            "backend": restart_backend_nssm,
            "swarm_api": restart_backend_nssm,
            "qdrant": alert_only,
        }

    async def recover(self, anomaly):
        source = anomaly.get("component") or anomaly.get("source")
        action = self.actions.get(source, alert_only)
        if callable(action):
            result = action(anomaly)
            return result if not hasattr(result, "__await__") else await result
        return {"ok": False, "reason": f"no recovery action for {source}"}