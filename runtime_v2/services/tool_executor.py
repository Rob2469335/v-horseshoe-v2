"""Routes tool calls to MCP handlers."""
import logging
from pathlib import Path

log = logging.getLogger(__name__)
import os
_ROOT = Path(os.getenv("ZENITH_PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent))

_mcp_manager = None
async def get_mcp_manager():
    global _mcp_manager
    if _mcp_manager is None:
        from swarm_os.lib.mcp.mcp_client import ExternalMCPClientManager
        _mcp_manager = ExternalMCPClientManager()
        await _mcp_manager.start()
    return _mcp_manager

async def run(tool_name: str, payload: dict) -> dict:
    try:
        if tool_name == "filesystem":
            from swarm_os.lib.mcp.filesystem import filesystem_handler
            import asyncio, inspect
            try:
                res_obj = filesystem_handler(payload, _ROOT)
                result = await asyncio.wait_for(res_obj, timeout=30.0) if inspect.isawaitable(res_obj) else res_obj
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "Filesystem operation timed out."}
        elif tool_name == "web_search":
            from swarm_os.lib.mcp.web_search import web_search_handler
            import asyncio, inspect
            try:
                res_obj = web_search_handler(payload)
                result = await asyncio.wait_for(res_obj, timeout=30.0) if inspect.isawaitable(res_obj) else res_obj
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "Web search timed out."}
        elif tool_name == "semantic_search":
            from runtime_v2.services.semantic_search import semantic_search
            query = payload.get("query", "")
            limit = int(payload.get("limit", 5))
            import asyncio
            text_result = await asyncio.to_thread(semantic_search, query, limit)
            result = {"ok": True, "result": text_result}
        elif tool_name == "remember":
            from runtime_v2.services.memory_core import remember_fact
            fact = payload.get("fact", "")
            category = payload.get("category", "general")
            import asyncio
            success = await asyncio.to_thread(remember_fact, fact, category)
            if success:
                result = {"ok": True, "result": f"Successfully remembered: {fact}"}
            else:
                result = {"ok": False, "error": "Failed to store memory in Qdrant."}
        elif tool_name == "sandbox_repl":
            from swarm_os.capabilities.sandbox_repl import SandboxReplHandler
            import asyncio, inspect
            try:
                res_obj = SandboxReplHandler().execute(payload)
                result = await asyncio.wait_for(res_obj, timeout=60.0) if inspect.isawaitable(res_obj) else res_obj
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "Execution timed out after 60 seconds."}
        elif tool_name == "vscode_automation":
            from swarm_os.capabilities.vscode_automation import VSCodeAutomationHandler
            import asyncio, inspect
            try:
                res_obj = VSCodeAutomationHandler(str(_ROOT)).execute(payload)
                result = await asyncio.wait_for(res_obj, timeout=30.0) if inspect.isawaitable(res_obj) else res_obj
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "VSCode automation timed out."}
        elif tool_name == "lsp":
            from swarm_os.capabilities.lsp_tool import LSPToolHandler
            import asyncio, inspect
            try:
                res_obj = LSPToolHandler().execute(payload)
                result = await asyncio.wait_for(res_obj, timeout=60.0) if inspect.isawaitable(res_obj) else res_obj
                # the result dict returned from LSPToolHandler has either {"result": ...} or {"error": ...}
                if "error" in result:
                    result = {"ok": False, "error": result["error"]}
                else:
                    result = {"ok": True, "result": result.get("result")}
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "LSP operation timed out."}
        elif tool_name == "playwright":
            from swarm_os.lib.mcp.playwright import playwright_handler
            import asyncio
            try:
                result = await asyncio.wait_for(playwright_handler(payload), timeout=60.0)
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "Playwright operation timed out."}
        elif tool_name == "mcp_register":
            import json
            from pathlib import Path
            config_path = _ROOT / "swarm_config.json"
            server_name = payload.get("server_name")
            command = payload.get("command")
            args = payload.get("args", [])
            
            if not server_name or not command:
                result = {"ok": False, "error": "server_name and command are required"}
            else:
                try:
                    config = {}
                    if config_path.exists():
                        def read_config():
                            with open(config_path, "r", encoding="utf-8") as f:
                                return json.load(f)
                        config = await asyncio.to_thread(read_config)
                    
                    config.setdefault("mcp_servers", {})
                    config["mcp_servers"][server_name] = {
                        "command": command,
                        "args": args
                    }
                    
                    def write_config():
                        with open(config_path, "w", encoding="utf-8") as f:
                            json.dump(config, f, indent=2)
                    await asyncio.to_thread(write_config)
                    
                    # Force restart of MCP manager to load new tool
                    global _mcp_manager
                    old_manager = _mcp_manager
                    _mcp_manager = None
                    if old_manager:
                        await old_manager.stop()
                        
                    # Pre-start it to verify tools
                    new_mgr = await get_mcp_manager()
                    new_tools = new_mgr.cached_tools
                    
                    result = {"ok": True, "result": f"Registered MCP server '{server_name}'. Now available tools: {[t['name'] for t in new_tools] if new_tools else []}"}
                except Exception as e:
                    result = {"ok": False, "error": f"Failed to register MCP server: {e}"}
        elif tool_name == "mcp":
            import asyncio
            manager = await get_mcp_manager()
            server_name = payload.get("server")
            mcp_tool = payload.get("tool")
            arguments = payload.get("arguments", {})
            try:
                if not server_name or not mcp_tool:
                    result = {"ok": False, "error": "MCP action requires 'server' and 'tool' arguments."}
                else:
                    call_res = await asyncio.wait_for(manager.call_tool(server_name, mcp_tool, arguments), timeout=60.0)
                    if hasattr(call_res, "content"):
                        text_output = "\n".join([c.text for c in call_res.content if hasattr(c, "text")])
                        result = {"ok": True, "result": text_output}
                    else:
                        result = {"ok": True, "result": str(call_res)}
            except KeyError as e:
                result = {"ok": False, "error": str(e)}
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "MCP operation timed out."}
            except Exception as e:
                result = {"ok": False, "error": f"MCP execution failed: {e}"}
        else:
            return {"ok": False, "error": f"Unknown tool: {tool_name}"}

        # Aggressive truncation for context window safety (traverse nested dicts)
        def _truncate(obj, limit=5000):
            if isinstance(obj, str):
                return obj if len(obj) <= limit else obj[:limit] + "\n\n...[OUTPUT TRUNCATED]..."
            elif isinstance(obj, dict):
                return {k: _truncate(v, limit) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [_truncate(v, limit) for v in obj]
            elif isinstance(obj, (int, float, bool, type(None))):
                return obj
            return str(obj)

        if isinstance(result, dict):
            result = _truncate(result)
            
        return result
    except Exception as exc:
        log.exception("Tool %s failed", tool_name)
        return {"ok": False, "error": str(exc)}
