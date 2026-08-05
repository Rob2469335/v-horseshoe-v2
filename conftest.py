collect_ignore = [
    "test_cli_request.py",
    "test_live_features.py",
]

collect_ignore_glob = [
    "scratch/*.txt",
]

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(autouse=True)
def global_reflexion_service_mock():
    """Make the reflexion-memory check inside the swarm brain hermetic and fast.

    The live `get_reflection_service()` hits the embedding service (port 8081)
    on every brain call, which 3-attempt-retries when the server is down —
    stalling evolutionary-kernel tests. Tests that exercise the REAL service
    create it directly (or patch this function locally), so this fake is only a
    default fallback and never runs real network I/O.
    """
    service = AsyncMock()
    service.check_for_past_mistakes = AsyncMock(return_value="")
    service.get_relevant_memories = AsyncMock(return_value=[])
    service.store_reflexion = AsyncMock(return_value=None)

    with patch("swarm_os.services.reflection_loop.get_reflection_service",
               return_value=service):
        yield service

