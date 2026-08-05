import asyncio
import logging
from typing import Callable, Any, Coroutine, Dict, List
from dataclasses import dataclass, field
import uuid

log = logging.getLogger(__name__)

@dataclass
class Event:
    topic: str
    payload: Any
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

class MessageBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Event], Coroutine[Any, Any, None]]]] = {}
        self._queue = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None

    def subscribe(self, topic: str, handler: Callable[[Event], Coroutine[Any, Any, None]]):
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(handler)
        log.debug(f"[MessageBus] Subscribed handler to '{topic}'")

    async def publish(self, event: Event):
        await self._queue.put(event)
        log.debug(f"[MessageBus] Published '{event.topic}' [corr_id: {event.correlation_id}]")

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._process_events())
        log.info("[MessageBus] Started processing loop")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("[MessageBus] Stopped processing loop")

    async def _process_events(self):
        while self._running:
            try:
                event = await self._queue.get()
                handlers = self._subscribers.get(event.topic, [])
                
                if not handlers:
                    log.debug(f"[MessageBus] No subscribers for '{event.topic}'")
                    self._queue.task_done()
                    continue

                # Run handlers concurrently
                tasks = []
                for handler in handlers:
                    tasks.append(asyncio.create_task(self._safe_execute(handler, event)))
                
                await asyncio.gather(*tasks)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception(f"[MessageBus] Critical error in event loop: {e}")

    async def _safe_execute(self, handler, event):
        try:
            await handler(event)
        except Exception as e:
            log.exception(f"[MessageBus] Error in handler for '{event.topic}': {e}")

global_bus = MessageBus()
