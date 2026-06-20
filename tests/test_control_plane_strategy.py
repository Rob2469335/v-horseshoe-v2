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
            ModelProfile(name="qwen2.5:3b-instruct", role="fast", max_tokens=32000),
        ],
        default_role="reasoning",
    )
    decision = await router.route_model(
        candidates=["qwen2.5:3b-instruct"],
        role="fast",
        allow_fallback=True,
    )
    assert isinstance(decision, RouteDecision)
    assert decision.strategy == "default"
    assert decision.model == "qwen2.5:3b-instruct"
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
async def test_orchestrator_trace_preserves_strategy():
    orchestrator = Orchestrator()
    await orchestrator.generate(model="qwen2.5:3b-instruct", prompt="hello")
    traces = orchestrator.get_recent_traces(limit=10)
    router_events = [event for event in traces if event.get("phase") == "router"]
    assert router_events
    assert router_events[-1]["metadata"]["strategy"] == "default"


