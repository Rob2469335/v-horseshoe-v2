"""
Module: session_service
Order: 30
Package: app.services
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from swarm_os.execution.agents.task_session import TaskSession


class SessionService:
    def create_session(self, task_name: str) -> TaskSession:
        return TaskSession(task_name=task_name)