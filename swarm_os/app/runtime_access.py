from typing import TYPE_CHECKING
from fastapi import Request

if TYPE_CHECKING:
    from swarm_os.app.main import RuntimeGraph


def get_runtime(request: Request) -> "RuntimeGraph":
    return request.app.state.runtime
