class SelectionEngine:
    def calculate_fitness(self, agent, outcome: dict) -> float:
        """
        Calculates fitness with a 'Readability Tax' for complex strategies.
        """
        base_fitness = outcome.get("fitness", 0)
        
        # Penalize overly complex strategies (more than 10 steps)
        if len(outcome.get("history", [])) > 10:
            base_fitness *= 0.8
            
        return base_fitness
