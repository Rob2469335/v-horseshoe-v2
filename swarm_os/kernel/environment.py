from __future__ import annotations
from typing import Any, Dict, List
import random

class Environment:
    def __init__(self):
        self.resources = {"compute": 50.0, "energy": 50.0}
        self.entropy = 0.0
        self.t = 0

    def tick(self):
        self.entropy += random.uniform(0.01, 0.03)
        self.resources["energy"] -= self.entropy * 0.1
        self.t += 1

    def apply(self, actions: List[Dict[str, Any]]):
        for a in actions:
            cost = float(a.get("cost", 0.1))
            cost = max(0.0, min(cost, 5.0))
            self.resources["compute"] = max(0.0, self.resources["compute"] - cost)

    def state(self):
        return {"resources": dict(self.resources), "entropy": self.entropy, "t": self.t}
