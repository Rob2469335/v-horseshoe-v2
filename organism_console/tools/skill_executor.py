class SkillExecutor:
    def __init__(self):
        self.execution_log = []

    def execute(self, action: str, task: str):
        print("[executor] Executing skill action")

        result = self._run_action(action, task)
        success = True

        self.execution_log.append({
            "task": task,
            "action": action,
            "success": success,
            "result": result
        })

        return result, success

    def _run_action(self, action: str, task: str):
        if "import error" in task.lower():
            return f"resolved_import:{task}"
        if "syntax error" in task.lower():
            return f"fixed_syntax:{task}"
        return f"executed:{task}"

    def get_execution_history(self):
        return self.execution_log
