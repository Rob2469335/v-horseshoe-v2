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


def _default_root() -> Path:
    """Stable sandbox root: the project root, NOT Path.cwd().

    cwd can change at runtime (a test or embedding that chdirs), and the old
    `Path.cwd()` default made the registry's filesystem root follow whatever
    directory the process happened to be in — so a filesystem read of
    'swarm_os/foo.py' failed after any chdir. The project root is deterministic:
    parents[3] from swarm_os/lib/mcp/registry.py = up from mcp -> lib -> swarm_os
    -> project root."""
    return Path(__file__).resolve().parents[3]


class MCPRegistry:
    def __init__(self, root: Path | None = None, trace_hook=None):
        self.root = (root or _default_root()).resolve()
        self.trace_hook = trace_hook or _noop_trace
        logger.info(f"Initialized MCPRegistry with root: {self.root}")

    async def call(self, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug(f"MCP call: {tool} with params: {params}")

        if tool == "filesystem":
            import asyncio

            # filesystem_handler is SYNC (blocking file I/O) — offload so a
            # recursive grep/list over the repo never blocks the event loop.
            return await asyncio.to_thread(
                filesystem_handler, params, self.root, self.trace_hook
            )

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
        # Enforce swarm_memory collection to prevent fragmentation and silent failures
        collection = "swarm_memory"
        top_k = int(params.get("top_k", params.get("limit", 5)))

        if not query:
            return {
                "ok": False,
                "error": "query is required",
                "results": [],
                "query": query,
                "collection": collection,
            }

        try:
            from swarm_os.services.embedding_service import EmbeddingService
            from swarm_os.services.vector_store import VectorStore

            if not getattr(self, "_qdrant", None):
                self._embedding = EmbeddingService()
                self._qdrant = VectorStore(collection_name=collection)
            vector = await self._embedding.embed(query)
            results = await self._qdrant.search(query_vector=vector, limit=top_k)

        except Exception as exc:
            logger.warning("qdrant_recall failed: %s", exc)
            results = []

        result = {
            "ok": True,
            "results": results,
            "query": query,
            "collection": collection,
        }
        self.trace_hook("qdrant_recall", result)
        return result

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "name": "filesystem",
                "description": "Read (or read_all/read_file), write, list, patch, or grep files in the local sandboxed workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": [
                                "read",
                                "read_file",
                                "read_all",
                                "write",
                                "patch",
                                "list",
                                "grep",
                            ],
                            "description": "The filesystem operation to perform ('read', 'read_file', or 'read_all' read file contents).",
                        },
                        "path": {
                            "type": "string",
                            "description": "Relative path to file or directory from project root within the workspace sandbox (e.g. 'runtime_v2/analyze_codebase.py').",
                        },
                        "content": {
                            "type": "string",
                            "description": "Complete text content for 'write' operation.",
                        },
                        "old": {
                            "type": "string",
                            "description": "The exact string segment to replace for 'patch' operation.",
                        },
                        "new": {
                            "type": "string",
                            "description": "The new string replacement content for 'patch' operation.",
                        },
                        "pattern": {
                            "type": "string",
                            "description": "Literal search pattern for 'grep' operation.",
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "Whether to grep recursively.",
                        },
                    },
                    "required": ["operation", "path"],
                },
            },
            {
                "name": "playwright",
                "description": "Execute browser automation using playwright.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "navigate",
                                "click",
                                "fill",
                                "screenshot",
                                "extract_text",
                            ],
                            "description": "The browser action to take.",
                        },
                        "url": {
                            "type": "string",
                            "description": "Target URL for navigate.",
                        },
                        "selector": {
                            "type": "string",
                            "description": "CSS selector for click/fill operations.",
                        },
                        "text": {"type": "string", "description": "Text to fill."},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "web_search",
                "description": "Search the web for real-time information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search engine query.",
                        }
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "context7",
                "description": "Fetch project context information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "Context retrieval scope.",
                        }
                    },
                    "required": ["scope"],
                },
            },
            {
                "name": "qdrant_recall",
                "description": "Recall memories and semantic data from the local Qdrant vector database.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to match against memories.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return (default 5).",
                        },
                    },
                    "required": ["query"],
                },
            },
        ]


registry = MCPRegistry()


async def call(tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return await registry.call(tool, params)
