"""Routes tool calls to MCP handlers."""
import logging
from pathlib import Path

log = logging.getLogger(__name__)
_ROOT = Path("C:/Users/rober/Projects/v-horseshoe-v2")

async def run(tool_name: str, payload: dict) -> dict:
    try:
        if tool_name == "filesystem":
            from swarm_os.lib.mcp.filesystem import filesystem_handler
            return await filesystem_handler(payload, _ROOT)
        if tool_name == "web_search":
            from swarm_os.lib.mcp.web_search import web_search_handler
            return await web_search_handler(payload)
        if tool_name == "sandbox_repl":
            from swarm_os.capabilities.sandbox_repl import SandboxReplHandler
            return await SandboxReplHandler().execute(payload)
        if tool_name == "vscode_automation":
            from swarm_os.capabilities.vscode_automation import VSCodeAutomationHandler
            return await VSCodeAutomationHandler(str(_ROOT)).execute(payload)
        return {"ok": False, "error": f"Unknown tool: {tool_name}"}
    except Exception as exc:
        log.exception("Tool %s failed", tool_name)
        return {"ok": False, "error": str(exc)}
