"""
Module: tool_contracts
Order: 20
Package: execution.tools
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolRequest:
    tool_name: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResponse:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
