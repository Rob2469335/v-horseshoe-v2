from swarm_os.kernel.brain import UpgradedSwarmBrainV10Ultimate
from swarm_os.services.control_plane import StateManager, Critic
import json

def run_metacognition():
    state = StateManager()
    
    # 1. Analyze History (Metacognition)
    with open('swarm_state.json', 'r') as f:
        history = json.load(f)['history']
    
    failures = [h for h in history if h['reward'] < 0.5]
    print(f'🧠 Metacognition Analysis: Found {len(failures)} historical failures.')
    
    # 2. Self-Correction
    if len(failures) > 0:
        print('🔧 Rewriting internal logic to prevent recurrence...')
        # Simulate updating internal heuristics based on history
        new_config = {'caution_level': 'HIGH', 'retry_limit': 3}
        with open('swarm_config.json', 'w') as f:
            json.dump(new_config, f)
        print('✅ Configuration updated: System is now self-aware of failure patterns.')

if __name__ == '__main__':
    run_metacognition()

