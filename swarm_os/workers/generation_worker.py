import logging
from ..core.message_bus import global_bus, Event
from ..infra.llama_client import LlamaClient

log = logging.getLogger(__name__)

class GenerationWorker:
    def __init__(self, bus=global_bus):
        self.bus = bus
        self.client = LlamaClient()
        self.bus.subscribe("GenerationRequested", self.handle_generation)

    async def handle_generation(self, event: Event):
        payload = event.payload
        model = payload.get("model", "qwen3.5-9b")
        messages = payload.get("messages", [])
        
        log.info(f"[GenerationWorker] Starting generation for model '{model}'")
        try:
            response_text = ""
            async for chunk in self.client.stream_generate(model=model, messages=messages):
                response_text += chunk
                
            await self.bus.publish(Event(
                topic="GenerationCompleted",
                payload={"model": model, "response": response_text},
                correlation_id=event.correlation_id
            ))
        except Exception as e:
            log.error(f"[GenerationWorker] Generation failed: {e}")
            await self.bus.publish(Event(
                topic="GenerationFailed",
                payload={"error": str(e)},
                correlation_id=event.correlation_id
            ))
