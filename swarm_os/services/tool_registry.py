import logging
import hashlib
import uuid
from typing import Dict
from qdrant_client.models import PointStruct, VectorParams, Distance
from swarm_os.services.embedding_service import EmbeddingService

log = logging.getLogger(__name__)

TOOL_SCHEMAS: Dict[str, dict] = {
    "web_search": {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    "playwright": {
        "type": "function",
        "function": {
            "name": "playwright_browse",
            "description": "Open a URL and extract page content using browser automation",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    "filesystem": {
        "type": "function",
        "function": {
            "name": "filesystem_read",
            "description": "Read a file from the local filesystem",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    "context7": {
        "type": "function",
        "function": {
            "name": "context7_lookup",
            "description": "Look up library or framework documentation",
            "parameters": {
                "type": "object",
                "properties": {
                    "library": {"type": "string"},
                    "query":   {"type": "string"},
                },
                "required": ["library", "query"],
            },
        },
    },
    "qdrant_recall": {
        "type": "function",
        "function": {
            "name": "qdrant_recall",
            "description": "Search long-term memory for relevant past context",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "collection": {
                        "type": "string",
                        "enum": ["chat_archive", "jobs", "files", "sessions"],
                    },
                },
                "required": ["query"],
            },
        },
    },
    "chat_search": {
        "type": "function",
        "function": {
            "name": "chat_search",
            "description": "Search past chat history and system logs",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                "required": ["query"],
            },
        },
    },
    "upwork_analyzer": {
        "type": "function",
        "function": {
            "name": "upwork_analyzer",
            "description": "Analyze an Upwork job description and draft a bid strategy",
            "parameters": {
                "type": "object",
                "properties": {"job_description": {"type": "string"}},
                "required": ["job_description"],
            },
        },
    },
    "vscode_automation": {
        "type": "function",
        "function": {
            "name": "vscode_automation",
            "description": "Automate VS Code workspace actions",
            "parameters": {
                "type": "object",
                "properties": {"action": {"type": "string"}, "args": {"type": "object"}},
                "required": ["action"],
            },
        },
    },
    "refactor": {
        "type": "function",
        "function": {
            "name": "refactor",
            "description": "Automated code refactoring tool",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}, "instructions": {"type": "string"}},
                "required": ["file_path", "instructions"],
            },
        },
    },
    "models": {
        "type": "function",
        "function": {
            "name": "models",
            "description": "Manage and query AI models",
            "parameters": {
                "type": "object",
                "properties": {"action": {"type": "string"}},
                "required": ["action"],
            },
        },
    },
    "sandbox_repl": {
        "type": "function",
        "function": {
            "name": "sandbox_repl",
            "description": "Execute code in a secure sandbox REPL",
            "parameters": {
                "type": "object",
                "properties": {"language": {"type": "string"}, "code": {"type": "string"}},
                "required": ["language", "code"],
            },
        },
    },
    "lsp": {
        "type": "function",
        "function": {
            "name": "lsp",
            "description": "Interact with Language Server Protocol for code intelligence",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}, "file_uri": {"type": "string"}},
                "required": ["command", "file_uri"],
            },
        },
    },
    "subagent": {
        "type": "function",
        "function": {
            "name": "subagent",
            "description": "Spawn a subagent to delegate a task",
            "parameters": {
                "type": "object",
                "properties": {"agent_id": {"type": "string", "enum": ["coordinator", "planner", "researcher", "executor", "coder", "tool-runner", "reviewer", "debugger"]}, "prompt": {"type": "string"}, "history": {"type": "array"}},
                "required": ["agent_id", "prompt"],
            },
        },
    },
    "cron_manage": {
        "type": "function",
        "function": {
            "name": "cron_manage",
            "description": "Programmatically list, create, or remove recurring background jobs",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "remove"]},
                    "task_id": {"type": "string"},
                    "goal": {"type": "string"},
                    "schedule": {"type": "string"}
                },
                "required": ["action"]
            }
        }
    },
    "skill_manage": {
        "type": "function",
        "function": {
            "name": "skill_manage",
            "description": "Programmatically interact with the swarm's skill registry",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "remove"]},
                    "skill_name": {"type": "string"},
                    "skill_content": {"type": "string"}
                },
                "required": ["action"]
            }
        }
    },
}

import asyncio
from qdrant_client import AsyncQdrantClient

class SemanticToolRegistry:
    def __init__(self, qdrant_url: str = "http://127.0.0.1:6333", collection_name: str = "agent_tools_registry"):
        self.client = AsyncQdrantClient(url=qdrant_url)
        self.collection = collection_name
        self.embedder = EmbeddingService()
        # Serializes _wait_init so a failed first init (Qdrant briefly down)
        # can't be re-entered by two concurrent callers running _init_registry
        # twice (two create_collection races). Created here so it's bound even
        # when constructed outside a running loop.
        self._init_lock = asyncio.Lock()
        try:
            loop = asyncio.get_running_loop()
            self._init_task = loop.create_task(self._init_registry())
        except RuntimeError:
            self._init_task = None
        self._ensured = False

    async def _wait_init(self):
        async with self._init_lock:
            if self._init_task:
                success = await self._init_task
                self._init_task = None
                if success:
                    self._ensured = True
            if not self._ensured:
                self._ensured = await self._init_registry()

    async def _init_registry(self) -> bool:
        self.initialized = False
        try:
            collections_response = await self.client.get_collections()
            collections = collections_response.collections
            if not any(c.name == self.collection for c in collections):
                await self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=768, distance=Distance.COSINE)
                )
                await self._populate_tools()
            self.initialized = True
            return True
        except Exception as e:
            log.error("Failed to initialize SemanticToolRegistry: %s", e)
            return False

    async def _populate_tools(self):
        points = []
        for i, (tool_name, schema) in enumerate(TOOL_SCHEMAS.items()):
            description = schema.get("function", {}).get("description", "")
            text_to_embed = f"tool: {tool_name} desc: {description}"
            vector = await self.embedder.embed(text_to_embed)
            point_id = str(uuid.UUID(hex=hashlib.sha256(tool_name.encode()).hexdigest()[:32]))
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={"tool_name": tool_name, "schema": schema, "pheromone_level": 1.0}
                )
            )
        if points:
            await self.client.upsert(collection_name=self.collection, points=points, wait=True)
            log.info("Populated SemanticToolRegistry with %d tools.", len(points))

    async def discover_tools(self, task_intent: str, top_k: int = 3) -> Dict[str, dict]:
        await self._wait_init()
        try:
            vector = await self.embedder.embed(task_intent)
            # qdrant-client >=1.18: AsyncQdrantClient has no .search(); use query_points.
            response = await self.client.query_points(
                collection_name=self.collection,
                query=vector,
                limit=top_k * 2,
            )
            results = getattr(response, "points", response)
            
            # Rerank combining semantic similarity score and pheromone multiplier
            reranked = sorted(
                results, 
                key=lambda x: ((x.score + 1) / 2) * float(x.payload.get('pheromone_level', 1.0)), 
                reverse=True
            )
            
            discovered = {}
            for hit in reranked[:top_k]:
                tool_name = hit.payload.get("tool_name")
                schema = hit.payload.get("schema")
                if tool_name and schema:
                    discovered[tool_name] = schema
            if not discovered:
                return {k: v for i, (k, v) in enumerate(TOOL_SCHEMAS.items()) if i < top_k}
            return discovered
        except Exception as e:
            log.warning("SemanticToolRegistry search failed: %s. Returning default tools.", e)
            return {k: v for i, (k, v) in enumerate(TOOL_SCHEMAS.items()) if i < top_k}
            
    async def update_tool_pheromone(self, tool_name: str, success: bool, alpha: float = 0.15, decay: float = 0.05):
        await self._wait_init()
        try:
            point_id = str(uuid.UUID(hex=hashlib.sha256(tool_name.encode()).hexdigest()[:32]))
            records = await self.client.retrieve(
                collection_name=self.collection,
                ids=[point_id]
            )
            if not records:
                return
            
            record = records[0]
            current_weight = float(record.payload.get("pheromone_level", 1.0))
            
            if success:
                new_weight = min(2.0, current_weight + alpha)
            else:
                new_weight = max(0.1, current_weight - decay)
                
            await self.client.set_payload(
                collection_name=self.collection,
                payload={"pheromone_level": new_weight},
                points=[record.id],
                wait=True
            )
            log.debug("Updated pheromone for %s: %.2f -> %.2f", tool_name, current_weight, new_weight)
        except Exception as e:
            log.warning("Failed to update tool pheromone for %s: %s", tool_name, e)

# Global registry instance
registry = None
def get_tool_registry() -> SemanticToolRegistry:
    global registry
    if registry is None:
        registry = SemanticToolRegistry()
    return registry
