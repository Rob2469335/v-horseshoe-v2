"""
zenith/zen_core.py - Unified Zenith Core (THE BRAIN)
Unifies all 5 subsystems into one cognitive runtime
"""
from typing import Dict, Any
from memory.zen_memory import ZenithMemory
from brain.brain_core import BrainCore
from runtime.execution_loop import ExecutionLoop
from swarm.swarm_core import SwarmCore


class ZenithCore:
    """Unified Zenith Core - THE BRAIN"""
    
    def __init__(self, root: str = "."):
        # Initialize all 5 subsystems
        self.memory = ZenithMemory(root)
        self.brain = BrainCore(self.memory)
        self.executor = ExecutionLoop()
        self.swarm = SwarmCore()
        
        # Build graph on init
        self.memory.build_graph()
    
    def run(self, task: str) -> Dict[str, Any]:
        """
        Run task: intent -> plan -> swarm review -> execute -> log
        This is THE CORE LOOP
        """
        # 1. Create plan (intent -> structured plan)
        plan = self.brain.create_plan(task)
        
        # 2. Swarm review (Planner -> Tester -> Fixer -> Reviewer)
        reviewed = self.swarm.run(task, plan)
        
        # Check if review passed
        if not reviewed["review"]["approved"]:
            return reviewed["review"]
        
        # 3. Execute plan (run -> fail -> fix -> retry)
        result = self.executor.run(reviewed["plan"])
        
        # 4. Log to memory (remember for future)
        self.memory.log_event({
            "task": task,
            "plan": plan,
            "result": result
        })
        
        return result
    
def get_core(root: str = ".") -> ZenithCore:
    """Get or create ZenithCore singleton"""
    global core_instance
    if core_instance is None:
        core_instance = ZenithCore(root)
    return core_instance
