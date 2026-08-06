import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# Extension -> language server command + correct languageId per LSP spec.
# Kept as a table so the languageId always matches the actual file language
# (the old client hardcoded "python" for every file, which broke go/rust
# diagnostics).
LANGUAGE_SERVERS: Dict[str, Dict[str, Any]] = {
    ".py": {"cmd": ["python", "-m", "pylsp"], "languageId": "python"},
    ".go": {"cmd": ["gopls"], "languageId": "go"},
    ".rs": {"cmd": ["rust-analyzer"], "languageId": "rust"},
}

# A warm LSP client is kept alive per extension so agents don't pay a ~1-2s
# subprocess cold start on every call (agent-lsp/claude-lsp-direct pattern).
IDLE_TTL_SECONDS = 30 * 60


class BasicLSPClient:
    """Long-lived LSP client with proper JSON-RPC framing.

    A background reader task owns the stdout pipe and dispatches messages:
    responses complete their correlated futures by message id, notifications
    are queued for consumers (e.g. textDocument/publishDiagnostics). A separate
    stderr drain keeps the child's stderr pipe from filling and deadlocking the
    server.
    """

    def __init__(self, command: list[str], root_path: str, language_id: str):
        self.command = command
        self.root_path = root_path
        self.language_id = language_id
        self.process: Optional[asyncio.subprocess.Process] = None
        self._next_msg_id = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._notifications: asyncio.Queue = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._write_lock = asyncio.Lock()
        self.last_used = 0.0

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._stderr_drain())
        await self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": Path(self.root_path).as_uri(),
                "capabilities": {},
            },
            timeout=30.0,
        )
        await self._notify("initialized", {})

    async def _read_loop(self):
        try:
            while True:
                msg = await self._read_message()
                if msg is None:
                    self._fail_pending(RuntimeError("LSP server closed the stream"))
                    return
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            error = msg.get("error", {})
                            fut.set_exception(
                                RuntimeError(
                                    error.get("message", json.dumps(error))
                                    if isinstance(error, dict)
                                    else str(error)
                                )
                            )
                        else:
                            fut.set_result(msg.get("result"))
                else:
                    self._notifications.put_nowait(msg)
        except asyncio.CancelledError:
            self._fail_pending(RuntimeError("LSP client reader cancelled"))
            raise
        except Exception as exc:
            log.debug("LSP reader loop exiting: %s", exc)
            self._fail_pending(RuntimeError(f"LSP reader failure: {exc}"))

    def _fail_pending(self, exc: Exception):
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _stderr_drain(self):
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    return
                log.debug("lsp[%s] stderr: %s", self.command[0], line.decode("utf-8", "replace").rstrip())
        except Exception:
            log.debug("LSP stderr drain error", exc_info=True)
            pass

    async def _read_message(self) -> Optional[dict]:
        try:
            headers = {}
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    return None
                if line == b"\r\n":
                    break
                parts = line.decode("utf-8", "replace").strip().split(": ", 1)
                if len(parts) == 2:
                    headers[parts[0].lower()] = parts[1]
            if "content-length" not in headers:
                return None
            length = int(headers["content-length"])
            body = await self.process.stdout.readexactly(length)
            return json.loads(body.decode("utf-8"))
        except Exception:
            return None

    async def _send(self, message: dict):
        payload = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("utf-8")
        async with self._write_lock:
            self.process.stdin.write(header + payload)
            await self.process.stdin.drain()

    async def _notify(self, method: str, params: dict):
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(self, method: str, params: dict, timeout: float = 30.0) -> Any:
        if not self.alive:
            raise RuntimeError("LSP server is not running")
        msg_id = self._next_msg_id
        self._next_msg_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
            async with asyncio.timeout(timeout):
                return await fut
        except TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"LSP request '{method}' timed out after {timeout}s")

    async def get_diagnostics(self, file_path: str, timeout: float = 8.0) -> list:
        uri = Path(file_path).as_uri()
        text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        await self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": self.language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )
        try:
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return []
                try:
                    async with asyncio.timeout(remaining):
                        msg = await self._notifications.get()
                except TimeoutError:
                    return []
                params = msg.get("params", {}) if isinstance(msg, dict) else {}
                if msg.get("method") == "textDocument/publishDiagnostics" and params.get("uri") == uri:
                    return params.get("diagnostics", [])
        finally:
            await self._notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    async def close(self):
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except BaseException:
                pass
            self._reader_task = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except BaseException:
                pass
            self._stderr_task = None
        if self.process is not None and self.process.returncode is None:
            try:
                self.process.terminate()
                async with asyncio.timeout(5.0):
                    await self.process.wait()
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    log.debug("Failed to kill LSP server process", exc_info=True)
                    pass
        self.process = None

    async def stop(self):
        await self.close()


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

_pool: Dict[str, BasicLSPClient] = {}
_pool_guard: Optional[asyncio.Lock] = None


def _get_pool_guard() -> asyncio.Lock:
    global _pool_guard
    if _pool_guard is None:
        _pool_guard = asyncio.Lock()
    return _pool_guard


async def _acquire_client(ext: str) -> BasicLSPClient:
    async with _get_pool_guard():
        client = _pool.get(ext)
        now = asyncio.get_running_loop().time()
        if client is not None:
            if not client.alive:
                client = None
            elif now - client.last_used > IDLE_TTL_SECONDS:
                try:
                    await client.close()
                except Exception:
                    log.debug("LSP client close failed for %s", ext, exc_info=True)
                    pass
        if client is None:
            cfg = LANGUAGE_SERVERS[ext]
            client = BasicLSPClient(cfg["cmd"], str(Path.cwd().resolve()), cfg["languageId"])
            await client.start()
            _pool[ext] = client
        client.last_used = now
        return client


async def _evict_client(ext: str, client: BasicLSPClient):
    async with _get_pool_guard():
        if _pool.get(ext) is client:
            del _pool[ext]
    try:
        await client.close()
    except Exception:
        log.debug("LSP evict-close failed for %s", ext, exc_info=True)
        pass


async def close_all():
    async with _get_pool_guard():
        clients = list(_pool.values())
        _pool.clear()
    for client in clients:
        try:
            await client.close()
        except Exception as exc:
            log.debug("Error closing LSP client: %s", exc)


class LSPToolHandler:
    def __init__(self):
        pass

    async def execute(self, payload: Any) -> Dict[str, Any]:
        ext = None
        client = None
        try:
            if not isinstance(payload, dict):
                return {"error": "Invalid payload format. Expected dict."}

            operation = payload.get("operation", "diagnostics")
            file_path = payload.get("file_path") or payload.get("path", "")

            if not file_path:
                return {"error": "Missing 'file_path'"}

            abs_path = Path(file_path).resolve()
            if not abs_path.exists():
                return {"error": f"File not found: {file_path}"}

            ext = abs_path.suffix.lower()
            if ext not in LANGUAGE_SERVERS:
                return {"error": f"Unsupported language extension: {ext}"}

            client = await _acquire_client(ext)

            if operation == "diagnostics":
                result = await client.get_diagnostics(str(abs_path))
            else:
                return {
                    "error": f"Operation '{operation}' not fully implemented. Only 'diagnostics' is supported currently."
                }

            return {"result": result}

        except Exception as e:
            if ext is not None and client is not None:
                await _evict_client(ext, client)
            return {"error": str(e)}
