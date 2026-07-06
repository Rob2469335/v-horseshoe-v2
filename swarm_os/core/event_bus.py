import asyncio
import time
import json
import os
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

LOG_PATH = os.path.join(os.getcwd(), ".swarm", "patch_log.jsonl")

class EventBus:
    def __init__(self):
        self.subscribers: List[asyncio.Queue] = []
        self.persistent_path = LOG_PATH
        os.makedirs(os.path.dirname(self.persistent_path), exist_ok=True)

    def emit(self, event_type: str, patch_id: str, payload: Dict[str, Any]):
        event = {
            "event": event_type,
            "id": patch_id,
            "timestamp": time.time(),
            "payload": payload
        }
        
        # 1. Persist to Disk (for history/recovery)
        try:
            with open(self.persistent_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception:
            pass  # Prevent race conditions or serialization errors from crashing the app
            
        # 2. Dispatch to SSE Subscribers (Real-time)
        self._dispatch_to_subscribers(event)

    def _dispatch_to_subscribers(self, event: Dict[str, Any]):
        # This part requires an active event loop for the subscribers' queues
        try:
            loop = asyncio.get_event_loop()
            for queue in list(self.subscribers):
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:
            # No event loop running in this thread (likely a sync script)
            pass

    async def subscribe(self):
        """Creates a new queue for a subscriber and yields events."""
        queue = asyncio.Queue()
        self.subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self.subscribers.remove(queue)

# Global Singleton
event_bus = EventBus()
