from __future__ import annotations
import asyncio
import sys

class SandboxReplHandler:
    async def execute(self, payload) -> dict:
        code = payload.get("code", "") if isinstance(payload, dict) else payload.code
        if not code.strip():
            return {"stdout": "", "stderr": "No code provided.", "exit_code": 1}
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            except asyncio.TimeoutError:
                proc.kill()
                return {"stdout": "", "stderr": "Execution timed out (10s limit).", "exit_code": 1}
            return {
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "exit_code": proc.returncode,
            }
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": 1}
