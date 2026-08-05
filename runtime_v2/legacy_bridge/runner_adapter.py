class RunnerAdapter:
    def __init__(self, legacy_service):
        self.legacy = legacy_service

    async def run(self, task, stream_state):
        return await self.legacy.step(task=task, state=stream_state)
