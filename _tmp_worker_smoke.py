import asyncio
from swarm_os.services.orchestrator import Orchestrator
from swarm_os.services.worker import SwarmWorker

async def main():
    o = Orchestrator()
    w = SwarmWorker(o)
    task = asyncio.create_task(w.run_loop())

    await asyncio.sleep(25)

    events = o.events.read_all()
    print("MID_EVENT_COUNT", len(events))
    print("MID_LAST_EVENTS", events[-5:])

    w.is_running = False
    await asyncio.sleep(2)

    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    events = o.events.read_all()
    print("DONE")
    print("FINAL_EVENT_COUNT", len(events))
    print("FINAL_LAST_EVENTS", events[-10:])

asyncio.run(main())

