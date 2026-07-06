import asyncio
import logging
import os
import sys

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test_adaptive")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runtime_v2.api.agent_service_v2 import _evaluate_task_complexity
from runtime_v2.services.model_registry import load_overrides

async def main():
    load_overrides()

    agent_id = "researcher"
    
    # Test 1: Easy Task
    easy_prompt = "analyze my codebase for bugs and improvements"
    log.info("Evaluating Easy Task: '%s'", easy_prompt)
    model1 = await _evaluate_task_complexity(easy_prompt, agent_id)
    log.info("Selected Model for Easy Task: %s", model1)

    # Test 2: Hard Task
    hard_prompt = "Please read the following 500 lines of python code in runtime_v2/api/agent_service_v2.py, analyze the abstract syntax tree for potential memory leaks in the asyncio event loop, and rewrite the core streaming generator to use websockets."
    log.info("Evaluating Hard Task: '%s'", hard_prompt)
    model2 = await _evaluate_task_complexity(hard_prompt, agent_id)
    log.info("Selected Model for Hard Task: %s", model2)

if __name__ == "__main__":
    asyncio.run(main())
