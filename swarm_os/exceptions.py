"""
exceptions.py - Custom exception classes for Swarm OS.
"""

from typing import Any


class ApprovalRequiredError(Exception):
    """
    Raised when a state-changing tool call is made without prior approval.
    """

    def __init__(self, tool_name: str, payload: Any):
        super().__init__(
            f"Approval required for tool '{tool_name}' with payload: {payload}"
        )
        self.tool_name = tool_name
        self.payload = payload
