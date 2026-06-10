from swarm_os.kernel.brain import UpgradedSwarmBrainV10Ultimate
from swarm_os.services.control_plane import StateManager
import random

def run_stress_test():
    brain = UpgradedSwarmBrainV10Ultimate(genome={'model': 'qwen2.5:7b'})
    state = StateManager()
    tasks = ['Audit kernel security', 'Optimize database indexing', 'Refactor network socket', 'Clean temporary cache', 'Generate system health report'] * 2
    
    print(f'🔥 Starting Stress Test: {len(tasks)} tasks...')
    for i, task in enumerate(tasks):
        result = brain({'task': task, 'retry_attempt': 0})
        state.save_result(task, result.composite_reward)
        if (i+1) % 5 == 0:
            print(f'✅ Progress: {i+1} tasks completed.')
    print('🏁 Stress Test Complete.')

if __name__ == '__main__':
    run_stress_test()

