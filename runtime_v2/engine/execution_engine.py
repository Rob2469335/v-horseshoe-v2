class ExecutionEngine:
    def __init__(self, runner_adapter):
        self.runner_adapter = runner_adapter

    async def execute(self, task, stream_state):
        return await self.runner_adapter.run(task, stream_state)
