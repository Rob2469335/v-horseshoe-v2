from .state import StateManager

class Guardian:
    def __init__(self, state_manager=None, threshold=0.75):
        self.state = state_manager or StateManager()
        self.threshold = threshold

    def monitor(self):
        metrics = self.state.get_recent_performance(window_size=5)
        avg_reward = metrics.get('avg_reward', 0)
        failures = metrics.get('failure_count', 0)
        
        print(f'🛡️ Guardian checking health. Avg Reward: {avg_reward:.4f} | Recent Failures: {failures}')
        
        if failures >= 2:
            print('🚨 ALERT: High failure rate detected. Triggering specific action: [Model Cooldown]...')
            self.trigger_cooldown()
        elif avg_reward < self.threshold and avg_reward > 0:
            print('⚠️ ALERT: Performance drifting. Triggering specific action: [Metacognition]...')
            self.trigger_metacognition()

    def trigger_cooldown(self):
        print("🔧 Action: Executing cooldown on failing models to prevent cascading errors...")
        # Future logic: tell the Router to stop using the failing model
        
    def trigger_metacognition(self):
        print("🧠 Action: Executing metacognition to adjust global strategy...")
        # os.system('python metacognition_test.py')

