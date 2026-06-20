from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from .filesystem import filesystem_handler
from .playwright import playwright_handler
from .context7 import context7_handler
from .web_search import web_search_handler

logger = logging.getLogger(__name__)

def _noop_trace(event: str, payload: Dict[str, Any]) -> None:
    return None

class MCPRegistry:
    def __init__(self, root: Path | None = None, trace_hook=None):
        self.root = (root or Path.cwd()).resolve()
        self.trace_hook = trace_hook or _noop_trace
        logger.info(f"Initialized MCPRegistry with root: {self.root}")

    async def call(self, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug(f"MCP call: {tool} with params: {params}")
        
        if tool == "filesystem":
            return await filesystem_handler(params, self.root, self.trace_hook)
        
        if tool == "playwright":
            return await playwright_handler(params, self.trace_hook)
            
        if tool == "context7":
            return await context7_handler(params, self.trace_hook)
            
        if tool == "web_search":
            return await web_search_handler(params, self.trace_hook)
            
        if tool == "qdrant_recall":
            return await self._qdrant_recall(params)
            
        return {"ok": False, "error": f"Unknown tool: {tool}"}

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
