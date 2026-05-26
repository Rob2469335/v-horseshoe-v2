"""
tool_runtime.py - Tool execution framework for Horseshoe Swarm.
Provides the CapabilityToolExecutor that routes tool calls to capability
handlers via CapabilityRouter, with caching and error handling.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from swarm_os.capabilities.capability_router import CapabilityRouter
from swarm_os.lib.mcp.registry import registry as mcp_registry

logger = logging.getLogger(__name__)


class CapabilityToolExecutor:
    """
    Executes tool calls by routing to capability handlers with namespace-isolated caching.
    """
    MCP_CAPABILITIES = {"filesystem", "playwright", "context7", "qdrant_recall", "web_search"}

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.router = CapabilityRouter(config=config)
        self._tool_cache: Dict[str, Any] = {}
        logger.info("Initialized CapabilityToolExecutor with router and MCP registry")

    def get_capabilities(self) -> list[str]:
        internal = set(self.router.list_capabilities())
        return sorted(internal | self.MCP_CAPABILITIES)

    def _normalize_payload(self, payload: Any) -> Dict[str, Any]:
        if payload is None:
            return {}
        if isinstance(payload, dict):
            return payload
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        if hasattr(payload, "dict"):
            return payload.dict()
        if hasattr(payload, "__dict__"):
            return {
                k: v for k, v in vars(payload).items()
                if not k.startswith("_")
            }
        return {"value": payload}

    async def execute_tool(
        self,
        capability_name: str,
        payload: Any,
        cache_key: Optional[str] = None
    ) -> Any:
        capability_name = capability_name.lower().strip()
        safe_key = f"{capability_name}:{cache_key}" if cache_key else None

        if safe_key and safe_key in self._tool_cache:
            logger.debug("Cache hit for key=%s", safe_key)
            return self._tool_cache[safe_key]

        try:
            logger.info(
                "Executing tool capability=%s payload_type=%s",
                capability_name,
                type(payload).__name__,
            )

            if capability_name in self.MCP_CAPABILITIES:
                normalized_payload = self._normalize_payload(payload)
                result = await mcp_registry.call(capability_name, normalized_payload)
            else:
                result = await self.router.execute(capability_name, payload)

            if safe_key:
                self._tool_cache[safe_key] = result
                logger.debug("Cached result for key=%s", safe_key)

            return result

        except KeyError as e:
            logger.error("Capability not found: %s", e)
            raise
        except Exception as e:
            logger.exception("Tool execution failed for capability=%s", capability_name)
            raise RuntimeError(f"Tool execution failed: {e}") from e

    def clear_cache(self) -> None:
        self._tool_cache.clear()
        logger.info("Cleared tool cache")

    def cache_size(self) -> int:
        return len(self._tool_cache)
