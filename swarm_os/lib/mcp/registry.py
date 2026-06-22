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
        collection = str(params.get("collection", "swarm_memory"))
        limit = int(params.get("limit", 5))

        if not query:
            return {"ok": False, "error": "Query is required"}

        try:
            from swarm_os.services.vector_store import VectorStore
            from swarm_os.services.embedding_service import EmbeddingService
            
            emb = EmbeddingService()
            vector = emb.embed(query)
            
            # Connect to local Qdrant instance
            store = VectorStore(collection_name=collection, use_memory=False)
            search_results = store.search(query_vector=vector, limit=limit)
            
            result = {
                "ok": True,
                "results": search_results,
                "query": query,
                "collection": collection,
            }
            if self.trace_hook:
                self.trace_hook("qdrant_recall", result)
            return result
        except Exception as e:
            logger.error(f"Vector recall failed: {e}")
            return {"ok": False, "error": str(e)}

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "name": "filesystem",
                "description": "Read, write, list, patch, or grep files in the local sandboxed workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["read", "write", "patch", "list", "grep"],
                            "description": "The filesystem operation to perform."
                        },
                        "path": {
                            "type": "string",
                            "description": "Relative path to file or directory within the workspace sandbox."
                        },
                        "content": {
                            "type": "string",
                            "description": "Complete text content for 'write' operation."
                        },
                        "old": {
                            "type": "string",
                            "description": "The exact string segment to replace for 'patch' operation."
                        },
                        "new": {
                            "type": "string",
                            "description": "The new string replacement content for 'patch' operation."
                        },
                        "pattern": {
                            "type": "string",
                            "description": "Literal search pattern for 'grep' operation."
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "Whether to grep recursively."
                        }
                    },
                    "required": ["operation", "path"]
                }
            },
            {
                "name": "playwright",
                "description": "Execute browser automation using playwright.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["navigate", "click", "fill", "screenshot", "extract_text"],
                            "description": "The browser action to take."
                        },
                        "url": {
                            "type": "string",
                            "description": "Target URL for navigate."
                        },
                        "selector": {
                            "type": "string",
                            "description": "CSS selector for click/fill operations."
                        },
                        "text": {
                            "type": "string",
                            "description": "Text to fill."
                        }
                    },
                    "required": ["action"]
                }
            },
            {
                "name": "web_search",
                "description": "Search the web for real-time information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search engine query."
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "context7",
                "description": "Fetch project context information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "Context retrieval scope."
                        }
                    },
                    "required": ["scope"]
                }
            },
            {
                "name": "qdrant_recall",
                "description": "Perform codebase semantic search using local vector storage.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to match semantically against codebase blocks."
                        },
                        "collection": {
                            "type": "string",
                            "description": "Vector store collection name (defaults to 'swarm_memory')."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of results to retrieve (default is 5)."
                        }
                    },
                    "required": ["query"]
                }
            }
        ]

registry = MCPRegistry()

async def call(tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return await registry.call(tool, params)

