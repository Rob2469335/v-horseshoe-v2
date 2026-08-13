from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

class ExternalMCPClientManager:
    """
    Manages active connections to external Model Context Protocol (MCP) servers.
    Reads server parameters from config, spawns processes, and exposes their tools.
    """
    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = str(Path(__file__).resolve().parents[3] / "swarm_config.json")
        self.config_path = Path(config_path)
        self.sessions: Dict[str, ClientSession] = {}
        self.exit_stack = None
        self.cached_tools = None

    async def start(self) -> List[Dict[str, Any]]:
        """Starts all configured external MCP servers and returns list of exposed tools."""
        if getattr(self, "cached_tools", None) is not None:
            return self.cached_tools

        if not self.config_path.exists():
            logger.warning(f"MCP config not found at: {self.config_path}")
            return []

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read MCP config: {e}")
            return []

        mcp_servers = config.get("mcp_servers", {})
        if not mcp_servers:
            logger.info("No external MCP servers configured.")
            return []

        all_tools = []
        from contextlib import AsyncExitStack
        self.exit_stack = AsyncExitStack()

        for name, cfg in mcp_servers.items():
            command = cfg.get("command")
            args = cfg.get("args", [])
            env = cfg.get("env", None)
            
            if not command:
                logger.warning(f"MCP server '{name}' missing command config.")
                continue

            try:
                logger.info(f"Starting external MCP server '{name}': {command} {' '.join(args)}")
                server_params = StdioServerParameters(
                    command=command,
                    args=args,
                    env={**os.environ, **(env or {})}
                )
                
                # Spawn stdio client transport
                read_stream, write_stream = await self.exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
                
                # Initialize session
                session = await self.exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                async with asyncio.timeout(30.0):
                    await session.initialize()
                
                # Retrieve tools
                tools_response = await session.list_tools()
                logger.info(f"Connected to MCP server '{name}' exposing {len(tools_response.tools)} tools.")
                
                self.sessions[name] = session
                for t in tools_response.tools:
                    # MCP SDK renamed inputSchema -> input_schema; accept both.
                    schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
                    all_tools.append({
                        "server": name,
                        "name": t.name,
                        "description": t.description or t.name,
                        "input_schema": schema,
                    })
            except Exception as e:
                logger.error(f"Failed to initialize MCP server '{name}': {e}")

        self.cached_tools = all_tools
        return all_tools

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool on a specific external MCP server."""
        session = self.sessions.get(server_name)
        if not session:
            raise KeyError(f"No active session for MCP server: {server_name}")
        
        logger.info(f"Calling external tool '{tool_name}' on server '{server_name}'...")
        async with asyncio.timeout(120.0):
            result = await session.call_tool(tool_name, arguments)
        return result

    async def stop(self):
        """Cleanly close all connections and shutdown subprocesses."""
        if self.exit_stack:
            try:
                await self.exit_stack.aclose()
                logger.info("Closed all external MCP server connections.")
            except Exception as e:
                logger.warning(f"Error while closing MCP connections: {e}")
            finally:
                self.sessions.clear()
                self.exit_stack = None
                self.cached_tools = None
