"""Routes tool calls to MCP handlers."""
import logging
from pathlib import Path

log = logging.getLogger(__name__)
_ROOT = Path("C:/Users/rober/Projects/v-horseshoe-v2")

async def run(tool_name: str, payload: dict) -> dict:
    try:
        if tool_name == "filesystem":
            from swarm_os.lib.mcp.filesystem import filesystem_handler
            result = await filesystem_handler(payload, _ROOT)
        elif tool_name == "web_search":
            from swarm_os.lib.mcp.web_search import web_search_handler
            result = await web_search_handler(payload)
        elif tool_name == "sandbox_repl":
            from swarm_os.capabilities.sandbox_repl import SandboxReplHandler
            result = await SandboxReplHandler().execute(payload)
        elif tool_name == "vscode_automation":
            from swarm_os.capabilities.vscode_automation import VSCodeAutomationHandler
            result = await VSCodeAutomationHandler(str(_ROOT)).execute(payload)
        else:
            return {"ok": False, "error": f"Unknown tool: {tool_name}"}

        # Aggressive truncation for context window safety
        if isinstance(result, dict):
            for k, v in result.items():
                if isinstance(v, str) and len(v) > 5000:
                    result[k] = v[:5000] + "\n\n...[OUTPUT TRUNCATED for Context Window Safety]..."
        return result
    except Exception as exc:
        log.exception("Tool %s failed", tool_name)
        return {"ok": False, "error": str(exc)}
