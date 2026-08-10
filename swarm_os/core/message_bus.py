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
        self._pending_tasks: set[asyncio.Task] = set()

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
        # Fire-and-forget handler tasks from the fan-out loop
        for t in list(self._pending_tasks):
            t.cancel()
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
            self._pending_tasks.clear()
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

                # Run handlers concurrently WITHOUT blocking the loop: a slow
                # subscriber must not head-of-line-block events on other topics
                # (previously `await asyncio.gather(*tasks)` held the next
                # queue.get() until the slowest handler finished).
                tasks = []
                for handler in handlers:
                    tasks.append(asyncio.create_task(self._safe_execute(handler, event)))
                self._pending_tasks.update(tasks)
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
        finally:
            self._pending_tasks.discard(asyncio.current_task())

global_bus = MessageBus()
