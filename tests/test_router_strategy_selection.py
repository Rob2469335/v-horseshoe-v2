from swarm_os.services.control_plane.models import ModelProfile
from swarm_os.services.control_plane.router import Router


def test_router_returns_deep_strategy_for_deep_role():
    router = Router(
        profiles=[
            ModelProfile(name="qwen2.5:3b-instruct", role="deep", max_tokens=32000),
        ],
        default_role="fast",
    )

    decision = router.route_model(
        candidates=["qwen2.5:3b-instruct"],
        role="deep",
        allow_fallback=True,
    )

    assert decision.strategy == "deep"
    assert decision.model == "qwen2.5:3b-instruct"
    assert decision.fallback is False


def test_router_keeps_default_strategy_for_fast_role():
    router = Router(
        profiles=[
            ModelProfile(name="qwen2.5:3b-instruct", role="fast", max_tokens=32000),
        ],
        default_role="fast",
    )

    decision = router.route_model(
        candidates=["qwen2.5:3b-instruct"],
        role="fast",
        allow_fallback=True,
    )

    assert decision.strategy == "default"
    assert decision.model == "qwen2.5:3b-instruct"
    assert decision.fallback is False
