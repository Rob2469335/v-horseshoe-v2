from swarm_os.kernel.brain import UpgradedSwarmBrainV10Ultimate
from swarm_os.services.control_plane import Planner, Critic, StateManager
import random

def run_chaos_test():
    brain = UpgradedSwarmBrainV10Ultimate(genome={'model': 'qwen2.5:7b'})
    planner = Planner()
    critic = Critic()
    state = StateManager()

    # Complex mission with a high-probability of 'soft' failure
    task = 'Audit system memory, identify leaks, and rewrite the critical path.'
    print(f'🔥 STARTING CHAOS MISSION: {task}')

    # Simulate a multi-step sequence where step 2 fails intentionally
    steps = ['Audit memory', 'REWRITE_KERNEL_PATH', 'Verify integrity']
    
    for step in steps:
        print(f'\n⚙️ Executing: {step}')
        
        # Inject simulated failure on the second step
        if step == 'REWRITE_KERNEL_PATH':
            print('⚠️ SIMULATED SYSTEM CRITICAL ERROR TRIGGERED.')
            simulated_output = {'content': 'CRITICAL_FAIL', 'status': 500}
        else:
            simulated_output = {'content': 'Success', 'status': 200}
            
        evaluation = critic.evaluate_step(simulated_output)
        
        if not evaluation.accepted:
            print('🛡️ Critic intercepted error. Triggering autonomous recovery...')
            # Logic: Tell the planner to bypass the failing step and fallback
            recovery_plan = planner.make_plan('Recover from kernel rewrite failure', {})
            print(f'✅ Recovery strategy deployed: {recovery_plan[0].goal}')
        
        state.save_result(step, 0.9 if evaluation.accepted else 0.1)

    print('\n🏁 Chaos Mission Complete.')

if __name__ == '__main__':
    run_chaos_test()

