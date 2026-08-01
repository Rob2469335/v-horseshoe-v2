import logging
from ..core.message_bus import global_bus, Event
from ..lib.mcp.registry import registry as mcp_registry

log = logging.getLogger(__name__)

class ToolWorker:
    def __init__(self, bus=global_bus):
        self.bus = bus
        self.mcp = mcp_registry
        self.bus.subscribe("ToolCallRequested", self.handle_tool_call)

    async def handle_tool_call(self, event: Event):
        payload = event.payload
        tool_name = payload.get("tool_name")
        params = payload.get("params", {})
        
        log.info(f"[ToolWorker] Executing tool '{tool_name}'")
        try:
            if tool_name == "command":
                observation = {
                    "ok": True,
                    "kind": "command",
                    "command": params.get("command"),
                    "handled": True,
                    "result": f"Slash command {params.get('command')} handled.",
                }
            else:
                observation = await self.mcp.call(tool_name, params)
            
            await self.bus.publish(Event(
                topic="ToolCallCompleted",
                payload={"tool_name": tool_name, "observation": observation, "status": "success"},
                correlation_id=event.correlation_id
            ))
        except Exception as e:
            log.error(f"[ToolWorker] Tool execution failed: {e}")
            await self.bus.publish(Event(
                topic="ToolCallFailed",
                payload={"tool_name": tool_name, "error": str(e)},
                correlation_id=event.correlation_id
            ))
