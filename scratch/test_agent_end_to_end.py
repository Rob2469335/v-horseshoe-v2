import sys
import os
import logging
import asyncio

import swarm_os.bootstrap
swarm_os.bootstrap.bootstrap()

import litellm
litellm.set_verbose = True
logging.basicConfig(level=logging.DEBUG)

from runtime_v2.api.agent_service_v2 import AgentServiceV2

async def main():
    service = AgentServiceV2()
    prompt = "Find the Open-Meteo API current weather temperature endpoint parameters for New York City"
    print(f"Starting test for prompt: {prompt}\n")
    agent_id = "executor"
    async for chunk in service.step_agent_stream(agent_id, prompt):
        print(chunk)

if __name__ == "__main__":
    asyncio.run(main())
