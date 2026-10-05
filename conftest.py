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
def isolate_prompt_repairer_store(tmp_path_factory):
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
    store = tmp_path_factory.mktemp("prompt_repairer_store")
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
def isolate_runtime_data_dirs(tmp_path_factory, monkeypatch):
    """Never let a test append to the runtime stores under `data/`.

    PROVEN leaks (docs/TEST_PERSISTENT_STORE_ISOLATION_AUDIT.md §13.1 and the
    broad-run follow-up): ordinary pytest wrote into production across at least
    four store families -- `data/trajectories/`, `data/run_snapshots/`,
    `data/evolution/staged/`, `data/repair_states/`, `data/snapshots/`,
    `data/chess/`, `data/intel/`, `data/search_quota.json` and
    `data/events/events.jsonl`.

    Cause is always the same shape as the Qdrant finding: a CWD-relative
    `Path("data/...")` resolved at import time, so under pytest (CWD == repo root)
    it points at production.

    Two mechanical constraints shaped this table:

    * Attributes are patched, not environment variables. Several of these paths
      are `Path(os.getenv("X", "data/..."))`, but that default is resolved at
      MODULE IMPORT -- before any fixture runs -- so setting the env var in a
      fixture is too late. The attribute has to be patched.
    * Patching is string-targeted so no module is imported at conftest load.

    When a new `data/` writer is added, add one line to `DATA_PATH_ATTRS`.
    That is the whole maintenance contract.
    """
    root = tmp_path_factory.mktemp("runtime_data")

    # (fully-qualified dotted path INCLUDING the attribute, relative subpath)
    data_path_attrs = [
        # trajectories / checkpoints / run snapshots
        ("runtime_v2.api.agent_service_v2.AgentServiceV2._TRAJ_DIR", "trajectories"),
        ("runtime_v2.services.checkpointing._CHECKPOINT_DIR", "checkpoints"),
        ("runtime_v2.services.run_snapshot._SNAPSHOT_DIR", "run_snapshots"),
        # evolution
        ("swarm_os.services.evolution_daemon.STAGED_DIR", "evolution/staged"),
        ("swarm_os.services.evolution_daemon.GENOMES_PATH", "evolution/genomes.jsonl"),
        ("runtime_v2.services.subagent_evolution.STAGED_DIR", "evolution/subagent_staged"),
        ("runtime_v2.services.tool_policy.OBSERVATIONS", "evolution/tool_observations.jsonl"),
        # repairs / snapshots
        ("organism_console.core.repair_state._REPAIR_STATE_DIR", "repair_states"),
        ("swarm_os.kernel.snapshot_index.SNAPSHOT_DIR", "snapshots"),
        # chess
        ("swarm_os.services.chess_games._DATA_DIR", "chess"),
        ("swarm_os.services.chess_mistakes._DATA_DIR", "chess"),
        ("swarm_os.services.chess_training._DATA_DIR", "chess"),
        ("swarm_os.services.chess_import._PROFILE_DIR", "chess"),
        ("swarm_os.services.chess_analysis_job._JOBS_DIR", "chess/analysis_jobs"),
        ("swarm_os.services.gm_games._DATA_DIR", "chess/gm"),
        # intel / usage
        ("swarm_os.services.competitive_intel._DATA_DIR", "intel"),
        ("runtime_v2.services.usage_log._USAGE_PATH", "usage/usage.jsonl"),
        ("runtime_v2.services.otel_telemetry._telemetry_path", "usage/otel_genai.jsonl"),
        # grants / tasks
        ("swarm_os.services.permission_tiers.GRANTS_FILE", "permission_grants.json"),
        ("swarm_os.services.trust_ledger._GRANTS_PATH", "trust_grants.json"),
        ("swarm_os.services.task_scheduler._TASKS_FILE", "tasks.json"),
        # events
        ("runtime_v2.services.canary_registry._REGISTRY_FILE", "events/canary_pending.json"),
        ("swarm_os.services.watch_loop._EVENTS_FILE", "events/events.jsonl"),
        ("swarm_os.services.watch_loop._HEARTBEAT_FILE", "events/watchman_heartbeat.json"),
        ("swarm_os.services.watch_loop._AUDIT_FILE", "events/auto_repairs.jsonl"),
        ("swarm_os.services.watch_loop._CANARY_HUMAN_REVIEW_FILE", "events/human_review.jsonl"),
        # legal
        ("swarm_os.services.legal.case_corpus.STATE_FILE", "legal/cases_ingest.json"),
        ("swarm_os.services.legal.case_tracker._CASES_DIR", "cases"),
        ("swarm_os.services.legal.citator.STATE_FILE", "legal/citator_state.json"),
        ("swarm_os.services.legal.trial_advisor.TRANSCRIPTS_DIR", "legal/transcripts"),
    ]

    for dotted, rel in data_path_attrs:
        monkeypatch.setattr(dotted, root / rel, raising=False)

    # `settings.snapshot_dir` is deliberately NOT patched: it lives on a frozen
    # dataclass, so neither production code nor a test can mutate it at runtime.
    # The writable snapshot paths are `snapshot_index.SNAPSHOT_DIR` (patched
    # above) and the repository classes that take a root argument.

    # These are resolved per call via os.getenv, so env vars do work here.
    monkeypatch.setenv("SWARM_SEARCH_QUOTA_FILE", str(root / "search_quota.json"))

    return root


@pytest.fixture(autouse=True)
def global_reflexion_service_mock():
    """Make the reflexion-memory check inside the swarm brain hermetic and fast.

    The live `get_reflection_service()` hits the embedding service (port 8081)
    on every brain call, which 3-attempt-retries when the server is down —
    stalling evolutionary-kernel tests. Tests that exercise the REAL service
    create it directly (or patch this function locally), so this fake is only a
    default fallback and never runs real network I/O.

    `check_model_reliability` is patched for the same reason and is the SAME
    bypass class as the Qdrant finding: `swarm_os/brain.py:247` imports it
    INSIDE the function, so patching `get_reflection_service` did not cover it.
    The real probe then hit the model endpoint (:8080) and retried, hanging
    `swarm_os/tests/integration/test_cycle.py` indefinitely with every thread
    pool worker blocked on `_run_isolated(...).result()`.
    """
    service = AsyncMock()
    service.check_for_past_mistakes = AsyncMock(return_value="")
    service.get_relevant_memories = AsyncMock(return_value=[])
    service.store_reflexion = AsyncMock(return_value=None)

    with patch(
        "swarm_os.services.reflection_loop.get_reflection_service", return_value=service
    ):
        with patch(
            "swarm_os.services.reflection_loop.check_model_reliability",
            return_value="",
            create=True,
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
