from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def _noop_trace(event: str, payload: Dict[str, Any]) -> None:
    return None


class MCPRegistry:
    def __init__(self, root: Path | None = None, trace_hook=None):
        self.root = (root or Path.cwd()).resolve()
        self.trace_hook = trace_hook or _noop_trace

    async def call(self, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if tool == "filesystem":
            return await self._filesystem(params)
        if tool == "qdrant_recall":
            return await self._qdrant_recall(params)
        return {"ok": False, "error": f"Unknown tool: {tool}"}

    def _resolve_in_sandbox(self, requested: str) -> Path:
        requested_path = Path(requested or "")
        target_path = (self.root / requested_path).resolve()
        try:
            target_path.relative_to(self.root)
        except ValueError as e:
            raise ValueError(f"Path is outside sandbox: {requested_path}") from e
        return target_path

    async def _filesystem(self, params: Dict[str, Any]) -> Dict[str, Any]:
        requested = str(params.get("path", ""))

        try:
            target_path = self._resolve_in_sandbox(requested)

            if not target_path.exists():
                result = {
                    "ok": False,
                    "error": f"File not found: {requested}",
                    "path": str(target_path),
                }
                self.trace_hook("filesystem_read", result)
                return result

            if not target_path.is_file():
                result = {
                    "ok": False,
                    "error": f"Not a file: {requested}",
                    "path": str(target_path),
                }
                self.trace_hook("filesystem_read", result)
                return result

            content = target_path.read_text(encoding="utf-8", errors="replace")
            result = {
                "ok": True,
                "path": str(target_path),
                "content": content,
            }
            self.trace_hook("filesystem_read", {"ok": True, "path": str(target_path)})
            return result

        except Exception as e:
            error_path = requested
            try:
                error_path = str((self.root / Path(requested)).resolve())
            except Exception:
                pass

            result = {
                "ok": False,
                "error": str(e),
                "path": error_path,
            }
            self.trace_hook("filesystem_read", result)
            return result

    async def _qdrant_recall(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = str(params.get("query", ""))
        collection = str(params.get("collection", ""))

        result = {
            "ok": True,
            "results": [],
            "query": query,
            "collection": collection,
        }
        self.trace_hook("qdrant_recall", result)
        return result


registry = MCPRegistry()


async def call(tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return await registry.call(tool, params)