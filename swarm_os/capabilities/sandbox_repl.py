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
            cmd = [sys.executable, "-c", str(code)]
            timeout = 30.0
        elif language == "powershell":
            cmd = ["pwsh", "-Command", str(command)]
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
                stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return {
                    "ok": proc.returncode == 0,
                    "stdout": stdout_bytes.decode("utf-8", errors="replace"),
                    "stderr": stderr_bytes.decode("utf-8", errors="replace"),
                    "returncode": proc.returncode if proc.returncode is not None else 0
                }
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
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
