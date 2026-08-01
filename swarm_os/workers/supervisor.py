import asyncio
import logging
import uuid
import json
from typing import AsyncGenerator
from ..core.message_bus import global_bus, Event

log = logging.getLogger(__name__)

class DecoupledSupervisor:
    """
    A pure Event-Driven Supervisor that replaces the Orchestrator God Object.
    It does not contain LLM generation logic or tool execution logic.
    It simply coordinates workers over the MessageBus.
    """
    def __init__(self, bus=global_bus):
        self.bus = bus
        self._streams = {}
        
        # Subscribe to worker outputs
        self.bus.subscribe("GenerationCompleted", self._on_gen_complete)
        self.bus.subscribe("GenerationFailed", self._on_gen_failed)
        self.bus.subscribe("ToolCallCompleted", self._on_tool_complete)

    async def _on_gen_complete(self, event: Event):
        trace_id = event.correlation_id
        if trace_id in self._streams:
            response = event.payload.get("response", "")
            
            # Simple Tool Parser logic (delegated to a separate parser normally)
            if "<tool_call" in response or '"tool"' in response:
                log.info(f"[Supervisor] Intercepted tool call, dispatching ToolWorker...")
                # In a real impl, parse the exact tool name and params
                await self.bus.publish(Event(
                    topic="ToolCallRequested",
                    payload={"tool_name": "example_tool", "params": {}},
                    correlation_id=trace_id
                ))
            else:
                await self._streams[trace_id].put(("done", response))

    async def _on_gen_failed(self, event: Event):
        trace_id = event.correlation_id
        if trace_id in self._streams:
            await self._streams[trace_id].put(("error", event.payload.get("error")))

    async def _on_tool_complete(self, event: Event):
        trace_id = event.correlation_id
        if trace_id in self._streams:
            obs = event.payload.get("observation")
            await self._streams[trace_id].put(("obs", obs))
            # Supervisor automatically requests next generation step
            await self.bus.publish(Event(
                topic="GenerationRequested",
                payload={"model": "qwen3.5-9b", "messages": [{"role": "user", "content": f"Observation: {obs}"}]},
                correlation_id=trace_id
            ))

    async def stream_task(self, model: str, messages: list[dict]) -> AsyncGenerator[str, None]:
        trace_id = str(uuid.uuid4())
        queue = asyncio.Queue()
        self._streams[trace_id] = queue
        
        log.info(f"[Supervisor] Dispatching task {trace_id} to workers")
        await self.bus.publish(Event(
            topic="GenerationRequested",
            payload={"model": model, "messages": messages},
            correlation_id=trace_id
        ))
        
        try:
            while True:
                kind, data = await queue.get()
                if kind == "error":
                    yield f"\\n[Error: {data}]"
                    break
                elif kind == "obs":
                    yield f"\\n[Tool Observation: {data}]"
                elif kind == "done":
                    yield data
                    break
        finally:
            del self._streams[trace_id]

# Singleton
supervisor = DecoupledSupervisor()
