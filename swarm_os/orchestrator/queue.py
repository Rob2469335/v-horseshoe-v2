class TaskQueue:
    def __init__(self):
        self.active_tasks = []

    def schedule(self, task):
        # Prevent task preemption: force completion before accepting new high-priority tasks
        if len(self.active_tasks) > 0:
            print("Swarm is in a learning cycle: finishing current task before pivoting.")
            return False
        self.active_tasks.append(task)
        return True
