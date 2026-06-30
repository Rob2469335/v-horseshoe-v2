import asyncio
import httpx
import time

async def main():
    print("Sending request to Ollama...")
    start = time.time()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://127.0.0.1:11434/api/generate",
                json={"model": "qwen3:8b-q4_K_M", "prompt": "Hello", "stream": False},
                timeout=30.0
            )
            print("Response:", resp.status_code)
            print(resp.text[:200])
    except Exception as e:
        print("Error:", e)
    print(f"Time: {time.time() - start:.2f}s")

asyncio.run(main())
