from runtime_v2.services.learning.meta_critic import MetaCritic
from runtime_v2.services.learning.critic_journal import CriticJournal

class EvolvingCritic:
    def __init__(self):
        self.critic = MetaCritic()
        self.journal = CriticJournal()

    def score(self, success: bool, confidence: float, predicted: float = 0.5):
        score = self.critic.score(success, confidence)

        data = {
            "predicted": predicted,
            "actual": success,
            "score": score,
            "weights": self.critic.weights.copy()
        }
        
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self.journal.log, data)
        except RuntimeError:
            self.journal.log(data)

        # learn from error (THIS is the evolution step)
        self.critic.learn(predicted, success)

        return score
