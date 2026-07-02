import asyncio
from runtime_v2.services.stream_runner import get_live_fallbacks
async def debug():
    raw = await get_live_fallbacks()
    print('RAW FALLBACKS:', raw)
asyncio.run(debug())
