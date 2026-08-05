from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ToolRequest:
    tool_name: str
    arguments: Dict[str, Any]
    request_id: str


@dataclass
class ToolResult:
    request_id: str
    success: bool
    output: Any = None
    error: str = None
