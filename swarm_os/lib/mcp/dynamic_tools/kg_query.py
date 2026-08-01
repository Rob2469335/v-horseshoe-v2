import os
import sys

# Ensure swarm_os is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from swarm_os.services.knowledge_graph import KnowledgeGraph

_kg = None

def get_schema() -> dict:
    return {
        "name": "kg_query",
        "description": "Query the RAG 2.0 active AST Knowledge Graph to find function and module dependencies (dependents and dependencies) spatially.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["dependencies", "dependents"],
                    "description": "Whether to query what this module depends on, or what depends on this module."
                },
                "module_name": {
                    "type": "string",
                    "description": "The python module path (e.g. 'swarm_os.services.vector_store')"
                },
                "depth": {
                    "type": "integer",
                    "description": "How deep to traverse the graph (default 1)."
                }
            },
            "required": ["action", "module_name"]
        }
    }

async def handle(params: dict, root_dir: str, trace_hook) -> dict:
    global _kg
    if _kg is None:
        _kg = KnowledgeGraph(root_dir)
        _kg.build_graph()

    action = params.get("action")
    module_name = params.get("module_name")
    depth = params.get("depth", 1)

    try:
        if action == "dependencies":
            result = _kg.query_dependencies(module_name, depth)
        elif action == "dependents":
            result = _kg.query_dependents(module_name, depth)
        else:
            return {"ok": False, "error": "Invalid action"}
            
        trace_hook("kg_query", {"module": module_name, "action": action})
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}
