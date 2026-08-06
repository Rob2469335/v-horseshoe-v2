import pytest
from swarm_os.services.control_plane.router import Router
from swarm_os.services.control_plane.models import ModelProfile

@pytest.mark.asyncio
async def test_router_self_healing_avoids_failed_model():
    p1 = ModelProfile(name='model-a', role='fast')
    p2 = ModelProfile(name='model-b', role='fast')
    router = Router(profiles=[p1, p2])
    candidates = ['model-a', 'model-b']
    router.record_failure('model-a', cooldown_seconds=60)
    decision = await router.route_model(candidates=candidates, role='fast')
    assert decision.model == 'model-b'
    router.record_failure('model-b', cooldown_seconds=60)
    decision2 = await router.route_model(candidates=candidates, role='fast')
    assert decision2.model in ['model-a', 'model-b']
    assert decision2.fallback is True
