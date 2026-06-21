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
            cmd = [r"C:\Python314\python.exe", "-c", str(code)]
            timeout = 30.0
        elif language == "powershell":
            cmd = ["pwsh", "-Command", str(command)]
            timeout = 30.0
        elif language == "pytest":
            cmd = [r"C:\Python314\python.exe", "-m", "pytest", str(path), "-v", "--tb=short"]
            timeout = 60.0
        else:
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"Unsupported language: {language}",
                "returncode": 1
            }

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return {
                    "ok": True,
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
