import asyncio
import logging
from runtime_v2.services.stream_runner import get_tool_decision

logging.basicConfig(level=logging.DEBUG)

async def main():
    messages = [{"role": "user", "content": "What is 2+2? Use a tool."}]
    try:
        decision = await get_tool_decision("qwen3:8b-q4_K_M", messages, "coordinator")
        print("DECISION:", decision)
    except Exception as e:
        print("EXCEPTION:", e)

if __name__ == "__main__":
    asyncio.run(main())
