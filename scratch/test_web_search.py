import sys
import os
import asyncio
import json

import swarm_os.bootstrap
swarm_os.bootstrap.bootstrap()

from runtime_v2.services.tool_executor import run as run_tool

async def main():
    result = await run_tool("web_search", {"query": "Open-Meteo API current weather temperature endpoint parameters for New York City"})
    print("RESULT:", json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
