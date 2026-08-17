import pytest
from swarm_os.services.control_plane.models import ModelProfile
from swarm_os.services.control_plane.router import Router


@pytest.mark.asyncio
async def test_router_returns_deep_strategy_for_deep_role():
    router = Router(
        profiles=[
            ModelProfile(name="qwen3.5-4b", role="deep", max_tokens=32000),
        ],
        default_role="reasoning",
    )

    decision = await router.route_model(
        candidates=["qwen3.5-4b"],
        role="deep",
        allow_fallback=True,
    )

    assert decision.strategy == "deep"
    assert decision.model == "qwen3.5-4b"
    assert decision.fallback is False


@pytest.mark.asyncio
async def test_router_keeps_default_strategy_for_fast_role():
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

    assert decision.strategy == "default"
    assert decision.model == "qwen3.5-4b"
    assert decision.fallback is False
