import pytest
from swarm_os.services.control_plane.models import ModelProfile, RouteDecision
from swarm_os.services.control_plane.router import Router
from swarm_os.services.control_plane.strategy import DefaultStrategy
from swarm_os.services.control_plane.strategy_registry import strategy_registry
from swarm_os.services.orchestrator import Orchestrator


def test_default_strategy_name():
    assert DefaultStrategy().name == "default"


def test_strategy_registry_returns_default():
    strategy = strategy_registry.get_active({"role": "fast"})
    assert strategy.name == "default"


@pytest.mark.asyncio
async def test_route_model_stamps_strategy():
    router = Router(
        profiles=[
            ModelProfile(name="qwen3.5-4b", role="fast", max_tokens=32000),
        ],
        default_role="reasoning",
    )
    decision = await router.route_model(
        candidates=["qwen3.5-4b"],
        role="fast",
        allow_fallback=True,
    )
    assert isinstance(decision, RouteDecision)
    assert decision.strategy == "default"
    assert decision.model == "qwen3.5-4b"
    assert decision.fallback is False


@pytest.mark.asyncio
async def test_route_model_fallback_no_candidates():
    router = Router(default_role="reasoning")
    decision = await router.route_model(
        candidates=[],
        role="fast",
        allow_fallback=True,
    )
    assert decision.strategy == "default"
    assert decision.fallback is True
    assert decision.reason == "no_candidates"


@pytest.mark.asyncio
async def test_orchestrator_trace_preserves_strategy(monkeypatch):
    orchestrator = Orchestrator()

    async def mock_generate(*args, **kwargs):
        return "mocked response"

    monkeypatch.setattr(orchestrator.llm, "generate", mock_generate)

    await orchestrator.generate(model="qwen3.5-4b", prompt="hello")
    traces = orchestrator.get_recent_traces(limit=10)
    router_events = [event for event in traces if event.get("phase") == "router"]
    assert router_events
    assert router_events[-1]["metadata"]["strategy"] == "default"


@pytest.mark.asyncio
async def test_concurrent_scoring_never_tears_last_penalty_score_pair():
    """Concurrent route_model calls run the strategy in a to_thread worker; the
    last_penalty/last_score pair-write must be atomic (guarded by the router's
    state lock) or a slow scoring pass could overwrite the penalty while a fast
    pass has already written its score — a torn (penalty_from_one, score_from_other)
    combination that corrupts the exported state the dashboards read.

    Revert-proof via lock-holding instrumentation: the write must happen while
    router._state_lock is held (pre-fix the router has no such lock, so scoring
    raises AttributeError instead of mutating under the guard)."""
    import threading
    from swarm_os.services.control_plane.models import ModelState

    router = Router(
        profiles=[ModelProfile(name="m1", role="fast", max_tokens=32000)],
        default_role="reasoning",
    )
    events: list[str] = []

    class RecordingLock:
        """Wraps a real threading.Lock; can't subclass Lock on CPython."""

        def __init__(self):
            self._lock = threading.Lock()

        def __enter__(self):
            events.append("enter")
            self._lock.acquire()
            return self

        def __exit__(self, *exc):
            events.append("exit")
            self._lock.release()
            return False

    router._state_lock = RecordingLock()
    strategy = DefaultStrategy()
    decision = strategy.select_model(
        router=router, candidates=["m1"], role="fast", allow_fallback=True
    )
    assert decision.model == "m1"
    # Every scoring mutation of the shared ModelState pair ran under the lock.
    assert events.count("enter") >= 1
    assert events.count("enter") == events.count("exit")
    state = router.get_state("m1")
    assert isinstance(state, ModelState)
    assert state.last_penalty == 0.0
    assert state.last_score > 0


def test_strategy_tolerates_none_and_missing_metadata_fields():
    """The metadata bonus fields (priority/tg128/pp512) must tolerate explicit
    None values AND keys missing entirely — a partial metadata dict from a
    shared_model_registry profile (or a hand-built one) must not crash routing.
    Pre-fix float(None) raised TypeError."""
    strategy = DefaultStrategy()
    router = Router(
        profiles=[
            ModelProfile(
                name="none-fields",
                role="fast",
                max_tokens=32000,
                metadata={"priority": None, "tg128": None, "pp512": None},
            ),
            ModelProfile(
                name="missing-fields",
                role="fast",
                max_tokens=32000,
                metadata={"some_unrelated_key": "x"},
            ),
            ModelProfile(
                name="no-metadata",
                role="fast",
                max_tokens=32000,
            ),
            ModelProfile(
                name="non-dict-metadata",
                role="fast",
                max_tokens=32000,
                metadata=None,
            ),
        ],
        default_role="reasoning",
    )
    for name in ("none-fields", "missing-fields", "no-metadata", "non-dict-metadata"):
        decision = strategy.select_model(
            router=router, candidates=[name], role="fast", allow_fallback=True
        )
        assert decision.model == name
        assert decision.fallback is False
