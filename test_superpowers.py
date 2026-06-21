import httpx
import asyncio
import json
import time

BASE_URL = 'http://127.0.0.1:8000/features'

async def test_stream(url, payload, name):
    print(f"\n{'='*60}\n🚀 INITIATING: {name} (Waiting for agent...)\n{'='*60}")
    last_received = time.time()
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            async with client.stream('POST', url, json=payload) as response:
                async for line in response.aiter_lines():
                    if line.startswith('data: '):
                        data = json.loads(line[6:])
                        print(data.get('content', ''), end='', flush=True)
                        last_received = time.time()
                    # Heartbeat: if silent for 5 seconds, print a dot
                    if time.time() - last_received > 5:
                        print('.', end='', flush=True)
                        last_received = time.time()
        except Exception as e:
            print(f"\nConnection ended: {e}")

async def main():
    await test_stream(f"{BASE_URL}/chat-search", {"query": "Briefly explain the Swarm OS loop"}, "LIBRARIAN")
    await test_stream(f"{BASE_URL}/upwork", {"query": "Find Python AI projects"}, "SCOUT")

if __name__ == '__main__':
    asyncio.run(main())
