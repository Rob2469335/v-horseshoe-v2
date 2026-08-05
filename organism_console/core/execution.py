from __future__ import annotations

import time

from .event_bus import event_bus
from .event_models import EventStatus, EventType, RuntimeEvent, ToolCall, ToolResult
from .tool_registry import registry


def now_ms() -> int:
    return int(time.time() * 1000)


def execute_tool(trace_id: str, call: ToolCall) -> ToolResult:
    spec = registry.get(call.tool_name)

    event_bus.publish(RuntimeEvent(
        trace_id=trace_id,
        event_type=EventType.tool_called,
        source="control_plane",
        target=call.tool_name,
        status=EventStatus.pending,
        payload={"arguments": call.arguments},
        timestamp_ms=now_ms()
    ))

    started = time.perf_counter()
    try:
        output = spec.handler(call.arguments)
        result = ToolResult(
            tool_name=call.tool_name,
            ok=True,
            output=output,
            duration_ms=(time.perf_counter() - started) * 1000
        )
        status = EventStatus.success
    except Exception as exc:
        result = ToolResult(
            tool_name=call.tool_name,
            ok=False,
            error=str(exc),
            duration_ms=(time.perf_counter() - started) * 1000
        )
        status = EventStatus.failed

    event_bus.publish(RuntimeEvent(
        trace_id=trace_id,
        event_type=EventType.tool_result,
        source="control_plane",
        target=call.tool_name,
        status=status,
        payload=result.model_dump(),
        timestamp_ms=now_ms()
    ))

    return result
