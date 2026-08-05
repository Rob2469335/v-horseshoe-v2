class CriticEngine:
    """
    Single authority for learning signal.
    No reviewer bias. No duplicate reinforcement.
    """

    def score(self, success: bool, confidence: float = 1.0) -> float:
        # hard gating: success is primary signal
        base = 1.0 if success else 0.0

        # critic smoothing (prevents noise spikes)
        score = (base * 0.8) + (confidence * 0.2)

        # punishment for failure
        if not success:
            score -= 0.4

        return max(0.0, min(1.0, score))
