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
            
        if tool == "terminal_exec":
            return await self._terminal_exec(params)
            
        if tool == "local_rag":
            return await self._local_rag(params)
            
        if tool == "ast_modifier":
            return await self._ast_modifier(params)
            
        return {"ok": False, "error": f"Unknown tool: {tool}"}

    async def _qdrant_recall(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        query = str(params.get("query", ""))
        # Enforce swarm_memory collection to prevent fragmentation and silent failures
        collection = "swarm_memory"
        top_k = int(params.get("top_k", params.get("limit", 5)))

        if not query:
            return {"ok": False, "error": "query is required", "results": [], "query": query, "collection": collection}

        try:
            from swarm_os.services.embedding_service import EmbeddingService
            from swarm_os.services.vector_store import VectorStore

            # Prevent blocking the async event loop with sync operations during initialization and query
            def _do_recall():
                if not getattr(self, '_qdrant', None):
                    self._embedding = EmbeddingService()
                    self._qdrant = VectorStore(collection_name=collection)
                vector = self._embedding.embed(query)
                return self._qdrant.search(query_vector=vector, limit=top_k)

            results = await asyncio.to_thread(_do_recall)
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

    async def _terminal_exec(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        command = params.get("command", "")
        if not command:
            return {"ok": False, "error": "command is required"}
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            return {
                "ok": True,
                "stdout": stdout.decode("utf-8", errors="ignore").strip(),
                "stderr": stderr.decode("utf-8", errors="ignore").strip(),
                "exit_code": proc.returncode
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _local_rag(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # Stub for semantic indexing
        return {"ok": True, "results": ["Local RAG index stubbed."]}

    async def _ast_modifier(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # Stub for tree-sitter modifier
        return {"ok": True, "results": ["AST modifier stubbed."]}

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
                "description": "Recall memories and semantic data from the local Qdrant vector database.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to match against memories."
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return (default 5)."
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "terminal_exec",
                "description": "Execute a shell command locally in the sandbox.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to run."
                        }
                    },
                    "required": ["command"]
                }
            },
            {
                "name": "local_rag",
                "description": "Perform semantic search across local codebase files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Code search query."
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "ast_modifier",
                "description": "Perform tree-sitter based code surgery.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": {
                            "type": "string",
                            "description": "Target file path."
                        },
                        "operation": {
                            "type": "string",
                            "description": "AST operation (e.g., 'replace_function', 'add_import')."
                        }
                    },
                    "required": ["file", "operation"]
                }
            }
        ]

registry = MCPRegistry()

async def call(tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return await registry.call(tool, params)
