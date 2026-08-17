import json
import os


class StateManager:
    def __init__(self, state_file="swarm_state.json"):
        self.state_file = state_file

    def record(self, outcome):
        # outcome is now a dictionary full of awesome details!
        data = self._load()
        data["history"].append(outcome)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        print(
            f"💾 Detailed State Recorded: {outcome.get('task', 'Unknown')} | Reward: {outcome.get('reward', 0):.4f}"
        )

    def get_recent_performance(self, window_size=5):
        data = self._load()
        recent = data["history"][-window_size:]
        if not recent:
            return {"avg_reward": 0, "failure_count": 0}

        avg_reward = sum(h.get("reward", 0) for h in recent) / len(recent)
        failure_count = sum(1 for h in recent if h.get("reward", 0) < 0.5)

        return {"avg_reward": avg_reward, "failure_count": failure_count}

    def _load(self):
        if not os.path.exists(self.state_file):
            return {"history": []}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {"history": []}
