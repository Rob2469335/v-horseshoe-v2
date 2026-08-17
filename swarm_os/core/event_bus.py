import asyncio
import time
import json
import os
import logging
from typing import Any, Dict, List

log = logging.getLogger(__name__)

LOG_PATH = os.path.join(os.getcwd(), ".swarm", "patch_log.jsonl")


class EventBus:
    def __init__(self):
        self.subscribers: List[asyncio.Queue] = []
        self.persistent_path = LOG_PATH
        self.main_loop = None
        os.makedirs(os.path.dirname(self.persistent_path), exist_ok=True)

    def emit(self, event_type: str, patch_id: str, payload: Dict[str, Any]):
        event = {
            "event": event_type,
            "id": patch_id,
            "timestamp": time.time(),
            "payload": payload,
        }

        # 1. Persist to Disk (for history/recovery)
        try:
            with open(self.persistent_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except Exception as e:
            log.error(f"Failed to persist event to disk: {e}")
            pass  # Prevent race conditions or serialization errors from crashing the app

        # 2. Dispatch to SSE Subscribers (Real-time)
        self._dispatch_to_subscribers(event)

    def _dispatch_to_subscribers(self, event: Dict[str, Any]):
        # This part requires the main event loop for the subscribers' queues
        if self.main_loop and not self.main_loop.is_closed():
            for queue in list(self.subscribers):
                self.main_loop.call_soon_threadsafe(queue.put_nowait, event)

    async def subscribe(self):
        """Creates a new queue for a subscriber and yields events."""
        if self.main_loop is None or self.main_loop.is_closed():
            self.main_loop = asyncio.get_running_loop()

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
