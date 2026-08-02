import os
import json
import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

class BasicLSPClient:
    def __init__(self, command: list[str], root_path: str):
        self.command = command
        self.root_path = root_path
        self.process = None
        self._msg_id = 1

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Send initialize
        init_req = {
            "jsonrpc": "2.0",
            "id": self._msg_id,
            "method": "initialize",
            "params": {
                "processId": os.getpid(),
                "rootUri": Path(self.root_path).as_uri(),
                "capabilities": {}
            }
        }
        self._msg_id += 1
        await self._send(init_req)
        await self._receive() # Read initialize response

    async def _send(self, message: dict):
        payload = json.dumps(message).encode('utf-8')
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode('utf-8')
        self.process.stdin.write(header + payload)
        await self.process.stdin.drain()

    async def _receive(self) -> Optional[dict]:
        try:
            # Read header
            headers = {}
            while True:
                line = await self.process.stdout.readline()
                if not line or line == b"\r\n":
                    break
                parts = line.decode('utf-8').strip().split(": ")
                if len(parts) == 2:
                    headers[parts[0]] = parts[1]
            
            if "Content-Length" not in headers:
                return None
                
            length = int(headers["Content-Length"])
            body = await self.process.stdout.readexactly(length)
            return json.loads(body.decode('utf-8'))
        except Exception as e:
            return None

    async def get_diagnostics(self, file_path: str) -> list:
        uri = Path(file_path).as_uri()
        # Open file
        await self._send({
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": uri,
                    "languageId": "python",
                    "version": 1,
                    "text": Path(file_path).read_text(encoding="utf-8", errors="ignore")
                }
            }
        })
        
        # Wait up to 3 seconds for textDocument/publishDiagnostics notification
        diagnostics = []
        for _ in range(10):
            try:
                # We use a short timeout read for diagnostics since it's a notification
                fut = self._receive()
                res = await asyncio.wait_for(fut, timeout=0.3)
                if res and res.get("method") == "textDocument/publishDiagnostics":
                    if res["params"]["uri"] == uri:
                        diagnostics = res["params"].get("diagnostics", [])
                        break
            except asyncio.TimeoutError:
                continue
        return diagnostics

    async def stop(self):
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
            await self.process.wait()


class LSPToolHandler:
    def __init__(self):
        pass

    async def execute(self, payload: Any) -> Dict[str, Any]:
        try:
            if not isinstance(payload, dict):
                return {"error": "Invalid payload format. Expected dict."}
                
            operation = payload.get("operation", "diagnostics")
            file_path = payload.get("file_path", "")
            
            if not file_path:
                return {"error": "Missing 'file_path'"}
                
            abs_path = Path(file_path).resolve()
            if not abs_path.exists():
                return {"error": f"File not found: {file_path}"}
                
            ext = abs_path.suffix.lower()
            if ext == ".py":
                cmd = ["python", "-m", "pylsp"]
            elif ext == ".go":
                cmd = ["gopls"]
            elif ext == ".rs":
                cmd = ["rust-analyzer"]
            else:
                return {"error": f"Unsupported language extension: {ext}"}
                
            client = BasicLSPClient(cmd, str(Path.cwd().resolve()))
            await client.start()
            
            try:
                if operation == "diagnostics":
                    result = await client.get_diagnostics(str(abs_path))
                else:
                    result = {"error": f"Operation '{operation}' not fully implemented. Only 'diagnostics' is supported currently."}
            finally:
                await client.stop()
                
            return {"result": result}
            
        except Exception as e:
            return {"error": str(e)}
