"""
core/engine.py - Main OmniDev Engine
"""
from typing import Dict, Any
from llm.router import ModelRouter
from runtime.execution_loop import ExecutionLoop
from swarm.agents import Swarm


class Engine:
    def __init__(self):
        self.router = ModelRouter()
        self.loop = ExecutionLoop()
        self.swarm = Swarm()
    
    def run(self, task: str) -> Dict[str, Any]:
        """Run a task with smart model routing"""
        # Route to best model
        model_result = self.router.route_smart(task)
        model = model_result["primary"]
        used_cloud = model_result["used_cloud"]
        
        print(f"Using model: {model} (cloud: {used_cloud})")
        
        # Run through swarm
        result = self.swarm.run(task, {"model": model})
        
        # Execute plan
        execution = self.loop.run(result["plan"])
        
        return {
            "task": task,
            "model": model,
            "used_cloud": used_cloud,
            "result": execution
        }
