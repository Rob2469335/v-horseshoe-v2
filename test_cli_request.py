import asyncio
from organism_console.api_client import SwarmClient
async def test():
    client = SwarmClient()
    try:
        async for event in client.stream_task('hello'):
            print(event)
    except Exception as e:
        print('CLI ERROR:', type(e), e)
asyncio.run(test())
