from swarm_os.kernel.brain import UpgradedSwarmBrainV10Ultimate
from swarm_os.services.control_plane import Planner, Critic, StateManager
import random

def run_integration():
    print('--- 🧠 Brain V10 Ultimate + Control Plane: INTEGRATION START ---')
    
    # Initialize Engine
    brain = UpgradedSwarmBrainV10Ultimate(genome={'model': 'qwen2.5:7b'})
    
    # Initialize Services
    planner = Planner()
    critic = Critic()
    state = StateManager()
    
    # Run a test task
    task = 'Analyze the code structure and provide a summary'
    print(f'Injecting task: {task}')
    
    # Execute Brain loop
    result = brain({'task': task, 'retry_attempt': 0})
    
    print(f'Brain Result: {result.content}')
    print(f'Composite Reward: {result.composite_reward}')
    print('--- ✅ INTEGRATION SUCCESSFUL ---')

if __name__ == '__main__':
    run_integration()

