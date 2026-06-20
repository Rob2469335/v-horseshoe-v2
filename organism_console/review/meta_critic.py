import json
from datetime import datetime

class MetaCritic:
    """
    Self-adjusting critic.
    Learns from prediction error, not from raw outcomes.
    """

    def __init__(self):
        self.weights = {
            "success_weight": 0.8,
            "confidence_weight": 0.2,
            "failure_penalty": 0.4
        }

    def score(self, success: bool, confidence: float = 1.0):
        base = 1.0 if success else 0.0

        score = (
            base * self.weights["success_weight"] +
            confidence * self.weights["confidence_weight"]
        )

        if not success:
            score -= self.weights["failure_penalty"]

        return max(0.0, min(1.0, score))

    def learn(self, predicted_score: float, actual_success: bool, learning_rate: float = 0.05):
        """
        Adjust weights based on error between prediction and reality.
        """

        actual = 1.0 if actual_success else 0.0
        error = actual - predicted_score

        # gradient-style adjustment (bounded evolution)
        self.weights["success_weight"] += learning_rate * error * 0.5
        self.weights["confidence_weight"] += learning_rate * error * 0.3
        self.weights["failure_penalty"] += learning_rate * (-error) * 0.2

        # clamp weights (prevents drift explosion)
        for k in self.weights:
            self.weights[k] = max(0.05, min(1.5, self.weights[k]))

        return self.weights
