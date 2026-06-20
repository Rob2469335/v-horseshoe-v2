from organism_console.review.meta_critic import MetaCritic
from organism_console.memory.critic_journal import CriticJournal

class EvolvingCritic:
    def __init__(self):
        self.critic = MetaCritic()
        self.journal = CriticJournal()

    def score(self, success: bool, confidence: float, predicted: float = 0.5):
        score = self.critic.score(success, confidence)

        self.journal.log({
            "predicted": predicted,
            "actual": success,
            "score": score,
            "weights": self.critic.weights.copy()
        })

        # learn from error (THIS is the evolution step)
        self.critic.learn(predicted, success)

        return score
