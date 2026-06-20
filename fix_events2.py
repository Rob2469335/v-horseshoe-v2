import pathlib

p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\api\routes.py')
src = p.read_text(encoding='utf-8')

# Add event_bus listener that mirrors to event_store on startup
old_health = '''@router.get("/health")
def health(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    return {"status": "ok", "overall": "healthy", "health_score": 100}'''

new_health = '''@router.get("/health")
def health(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    return {"status": "ok", "overall": "healthy", "health_score": 100}

@router.get("/events/stream")
async def events_stream(runtime: Any = Depends(runtime_dep)):
    """SSE stream of all organism events including Zenith agent activity."""
    from swarm_os.core.event_bus import event_bus
    from fastapi.responses import StreamingResponse
    import json
    async def _gen():
        async for event in event_bus.subscribe():
            yield f"data: {json.dumps(event)}\\n\\n"
    return StreamingResponse(_gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})'''

if old_health in src:
    src = src.replace(old_health, new_health)
    print("Events stream route added OK")
else:
    print("Health route not matched")

p.write_text(src, encoding='utf-8')
print("Done")
