"""
Module: tool_registry
Order: 19
Package: execution.tools
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    enabled: bool = True
    metadata: dict[str, Any] | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_enabled(self) -> list[ToolDefinition]:
        return [tool for tool in self._tools.values() if tool.enabled]
