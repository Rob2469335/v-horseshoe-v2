from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import json
import asyncio
from swarm_os.core.event_bus import event_bus

router = APIRouter()


async def event_generator():
    """
    Subscribes to the live EventBus and yields events for SSE.
    """
    subscriber = event_bus.subscribe()
    try:
        while True:
            try:
                # UPGRADE: asyncio.timeout() — composable context manager
                async with asyncio.timeout(15.0):
                    event = await anext(subscriber)
                yield f"data: {json.dumps(event, default=str)}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
            except StopAsyncIteration:
                break
    finally:
        await subscriber.aclose()


@router.get("/swarm/v10/stream")
async def swarm_v10_stream():
    """
    Production-grade SSE endpoint for Swarm V10 telemetry.
    Instantly delivers events from the EventBus.
    """
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        },
    )
