class OutputEvaluator:
    def evaluate(self, task: str, output, error=None) -> bool:
        # Hard failure rules
        if error is not None:
            return False

        if output is None:
            return False

        output_str = str(output).lower()

        # Heuristic signals of failure
        failure_signals = [
            "error",
            "exception", 
            "traceback",
            "failed",
            "invalid",
            "unsupported",
            "cannot",
            "not found"
        ]

        if any(sig in output_str for sig in failure_signals):
            return False

        # Task-specific heuristics
        task_lower = task.lower()
        
        # Import error tasks: look for success indicators
        if "import error" in task_lower:
            success_signals = ["success", "fixed", "resolved", "imported", "okay"]
            return any(sig in output_str for sig in success_signals)
        
        # Syntax error tasks
        if "syntax error" in task_lower:
            success_signals = ["fixed", "corrected", "syntax ok", "valid"]
            return any(sig in output_str for sig in success_signals)
        
        # Broken tasks (explicit failure)
        if "broken" in task_lower:
            return False

        # Default: assume success if no failure signals
        return True
