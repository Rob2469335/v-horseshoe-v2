import logging
import hashlib
import uuid
from typing import Dict, List, Any
from qdrant_client import QdrantClient
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
    "code_exec": {
        "type": "function",
        "function": {
            "name": "code_exec",
            "description": "Extract, validate, and optionally run a code block",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string"},
                    "code":     {"type": "string"},
                },
                "required": ["language", "code"],
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
}

class SemanticToolRegistry:
    def __init__(self, qdrant_url: str = "http://127.0.0.1:6333", collection_name: str = "agent_tools_registry"):
        self.client = QdrantClient(url=qdrant_url)
        self.collection = collection_name
        self.embedder = EmbeddingService()
        self._init_registry()

    def _init_registry(self):
        self.initialized = False
        try:
            collections = self.client.get_collections().collections
            if not any(c.name == self.collection for c in collections):
                self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=768, distance=Distance.COSINE)
                )
                self._populate_tools()
            self.initialized = True
        except Exception as e:
            log.error("Failed to initialize SemanticToolRegistry: %s", e)

    def _populate_tools(self):
        points = []
        for i, (tool_name, schema) in enumerate(TOOL_SCHEMAS.items()):
            description = schema.get("function", {}).get("description", "")
            text_to_embed = f"tool: {tool_name} desc: {description}"
            vector = self.embedder.embed(text_to_embed)
            point_id = str(uuid.UUID(hex=hashlib.sha256(tool_name.encode()).hexdigest()[:32]))
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={"tool_name": tool_name, "schema": schema, "pheromone_level": 1.0}
                )
            )
        if points:
            self.client.upsert(collection_name=self.collection, points=points, wait=True)
            log.info("Populated SemanticToolRegistry with %d tools.", len(points))

    def discover_tools(self, task_intent: str, top_k: int = 3) -> Dict[str, dict]:
        if not getattr(self, 'initialized', False):
            self._init_registry()
            
        try:
            vector = self.embedder.embed(task_intent)
            results = self.client.search(
                collection_name=self.collection,
                query_vector=vector,
                limit=top_k * 2
            )
            
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
            
    def update_tool_pheromone(self, tool_name: str, success: bool, alpha: float = 0.15, decay: float = 0.05):
        try:
            point_id = str(uuid.UUID(hex=hashlib.sha256(tool_name.encode()).hexdigest()[:32]))
            records = self.client.retrieve(
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
                
            self.client.set_payload(
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
