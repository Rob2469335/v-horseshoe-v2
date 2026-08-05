from __future__ import annotations
import asyncio
import sys

class SandboxReplHandler:
    async def execute(self, payload) -> dict:
        if isinstance(payload, dict):
            language = payload.get("language", "python")
            code = payload.get("code", "")
            command = payload.get("command", "")
            path = payload.get("path", "")
        else:
            language = getattr(payload, "language", "python")
            code = getattr(payload, "code", "")
            command = getattr(payload, "command", "")
            path = getattr(payload, "path", "")

        language = str(language).lower().strip()

        if language == "python":
            # SECURITY: AST-scan LLM/agent-supplied code before execution. The
            # sandbox is NOT a real isolation boundary (runs as the user, cwd=root),
            # so block banned calls/modules (exec/eval/subprocess/os/sys/socket/...)
            # deterministically instead of pretending it is safe.
            try:
                from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation
                SecurityGate.scan_code(str(code))
            except SecurityGateViolation as e:
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"Security Gate blocked execution: {e}",
                    "returncode": 1,
                }
            cmd = [sys.executable, "-I", "-c", str(code)]
            timeout = 30.0
        elif language == "powershell":
            # SECURITY: PowerShell has no clean AST-scan analog here, so gate the
            # command string with a conservative denylist of destructive/system-
            # mutating verbs (Remove/Stop/Set/Fmt/New-Service/reg/disk/...).
            blocked_ps = (
                "remove-", "stop-", "set-", "format-", "new-", "start-", "restart-",
                "del ", "rm ", "rd ", "erase", "kill", "taskkill", "shutdown",
                "format", "reg delete", "diskpart", "takeown", "icacls", "attrib +",
                "install-", "uninstall-", "out-file", "set-content", "add-content",
                "copy-item", "move-item", "rename-item", ">", ">>", "|",
            )
            ps_lower = str(command or "").lower()
            if any(b in ps_lower for b in blocked_ps):
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "Security Gate blocked PowerShell command (destructive/system-mutating operations are not allowed).",
                    "returncode": 1,
                }
            cmd = ["pwsh", "-NoProfile", "-Command", str(command)]
            timeout = 30.0
        elif language == "pytest":
            cmd = [sys.executable, "-m", "pytest", str(path), "-v", "--tb=short"]
            timeout = 60.0
        else:
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"Unsupported language: {language}",
                "returncode": 1
            }

        import os
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_root
            )
            try:
                async with asyncio.timeout(timeout):
                    stdout_bytes, stderr_bytes = await proc.communicate()
                return {
                    "ok": proc.returncode == 0,
                    "stdout": stdout_bytes.decode("utf-8", errors="replace"),
                    "stderr": stderr_bytes.decode("utf-8", errors="replace"),
                    "returncode": proc.returncode if proc.returncode is not None else 0
                }
            except TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                await proc.wait()
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"Execution timed out ({timeout}s limit).",
                    "returncode": -1
                }
        except Exception as e:
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": 1
            }
