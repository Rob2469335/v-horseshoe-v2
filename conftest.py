collect_ignore = [
    "test_cli_request.py",
    "test_live_features.py",
]

collect_ignore_glob = [
    "scratch/*.txt",
]

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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

    with patch(
        "swarm_os.services.reflection_loop.get_reflection_service", return_value=service
    ):
        yield service


@pytest.fixture(autouse=True)
def global_lesson_manager_mock():
    """Default-hermetic governed lesson seam.

    The Prompt Repairer seam (stream_runner / agent_service_v2) calls
    ``get_lesson_manager().render_active_lessions(...)`` on every worker
    decision. Against live Qdrant that 404s the (possibly-not-yet-created)
    ``ActiveLessons`` collection, which is correct fail-closed behaviour but
    noisy/slow in tests. Poison the singleton so tests get a deterministic
    empty block unless a test deliberately builds/patchs the real manager.
    """
    manager = MagicMock()
    manager.render_active_lessons = AsyncMock(return_value="")
    manager.retrieve = AsyncMock(return_value=[])
    manager.get_all = AsyncMock(return_value=[])
    manager.store = AsyncMock(return_value="lesson-mock")
    manager.remove = AsyncMock(return_value=True)

    with patch(
        "swarm_os.services.lesson_manager.get_lesson_manager", return_value=manager
    ):
        yield manager
