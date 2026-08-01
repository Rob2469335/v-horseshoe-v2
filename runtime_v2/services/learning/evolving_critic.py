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

        # BUG FIX: Retain the journal future so exceptions don't vanish silently,
        # and avoid unhandled-future warnings on GC.
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(None, self.journal.log, data)
            future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        except RuntimeError:
            self.journal.log(data)

        # learn from error (THIS is the evolution step)
        self.critic.learn(predicted, success)

        # BUG FIX: Return the full evolution payload (weights) too, so the caller
        # can emit a meaningful critic_update event instead of a bare float.
        return {"score": score, "weights": self.critic.weights.copy()}
