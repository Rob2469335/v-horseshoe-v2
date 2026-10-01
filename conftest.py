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
def isolate_prompt_repairer_store(tmp_path):
    """Never let a test mutate PromptRepairer's PRODUCTION persistent state.

    RED-1/RED-2 from `docs/TEST_PERSISTENT_STORE_ISOLATION_AUDIT.md`:
    `swarm_os/app/main.py:126-129` runs `recover_interrupted_promotions()` in the
    application lifespan, so ANY `TestClient(app)` construction replays the real
    production journal and then calls `_save_candidates()`. With pending journal
    rows present that appended ~142 `RECOVERED_INTERRUPTED_PROMOTION` records to
    the production audit log and rewrote the production candidate store — with no
    test involved at all.

    Every PromptRepairer write resolves its destination from the module globals
    `_DATA_DIR` / `_CANDIDATES_FILE` / `_SNAPSHOTS_FILE` / `_AUDIT_LOG_FILE` at
    CALL time (e.g. `prompt_repairer.py:1022`, `:1087`, and `_journal_file()` at
    `:66-69` which derives from `_DATA_DIR`), so redirecting those globals is
    sufficient even for the cached `get_prompt_repairer()` singleton.

    This ISOLATES rather than disables: the real recovery logic, real JSON/JSONL
    I/O, real `_save_candidates()` and real `_audit()` all still execute — against
    per-test temporary state. Nothing is mocked away.

    Lives at the repository root so it covers BOTH `tests/` and `swarm_os/tests/`;
    `tests/conftest.py` is scoped to `tests/` only. Mirrors the existing
    `isolate_outcome_fitness` pattern in `tests/conftest.py`.
    """
    store = tmp_path / "prompt_repairer_store"
    store.mkdir(parents=True, exist_ok=True)
    with patch("swarm_os.services.prompt_repairer._DATA_DIR", store):
        with patch(
            "swarm_os.services.prompt_repairer._CANDIDATES_FILE",
            store / "candidates.json",
        ):
            with patch(
                "swarm_os.services.prompt_repairer._SNAPSHOTS_FILE",
                store / "snapshots.json",
            ):
                with patch(
                    "swarm_os.services.prompt_repairer._AUDIT_LOG_FILE",
                    store / "audit.jsonl",
                ):
                    yield store


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
