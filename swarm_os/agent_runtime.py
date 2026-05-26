"""
agent_runtime.py - Agent runtime for Horseshoe Swarm.
Provides AgentRuntime that gives agents access to capability tools
through the CapabilityToolExecutor.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from swarm_os.tool_runtime import CapabilityToolExecutor

logger = logging.getLogger(__name__)

class AgentRuntime:
    """
    Runtime environment for agent execution with tool access.
    """
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.tool_executor = CapabilityToolExecutor(config=config)
        self._active_tools: set[str] = set(self.tool_executor.get_capabilities())
        logger.info("Initialized AgentRuntime with %d active tools", len(self._active_tools))

    def list_tools(self) -> List[str]:
        return list(self._active_tools)

    def enable_tool(self, capability_name: str) -> None:
        capability_name = capability_name.lower().strip()
        available = self.tool_executor.get_capabilities()
        if capability_name not in available:
            raise KeyError(f"Tool '{capability_name}' not available. Available: {available}")
        self._active_tools.add(capability_name)
        logger.info("Enabled tool '%s'", capability_name)

    def disable_tool(self, capability_name: str) -> None:
        capability_name = capability_name.lower().strip()
        if capability_name in self._active_tools:
            self._active_tools.remove(capability_name)
            logger.info("Disabled tool '%s'", capability_name)

    async def call_tool(self, capability_name: str, payload: Any, cache_key: Optional[str] = None) -> Any:
        """
        Call a tool by capability name, correctly passing cache keys to execution engine.
        """
        capability_name = capability_name.lower().strip()
        if capability_name not in self._active_tools:
            raise RuntimeError(
                f"Tool '{capability_name}' is disabled. "
                f"Active tools: {list(self._active_tools)}"
            )

        # Audit Correction: cache_key must be passed through to tool executor loop explicitly!
        return await self.tool_executor.execute_tool(capability_name, payload, cache_key=cache_key)

    def get_tool_cache_size(self) -> int:
        return self.tool_executor.cache_size()

    def clear_tool_cache(self) -> None:
        self.tool_executor.clear_cache()
        logger.info("Cleared agent runtime tool cache")
