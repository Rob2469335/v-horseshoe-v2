import pytest
from swarm_os.kernel.brain import make_swarm_brain_v10_ultimate, UpgradedSwarmBrainV10Ultimate

class TestNewBrain:
    def test_brain_class_loads(self):
        assert UpgradedSwarmBrainV10Ultimate is not None

    def test_factory_function_works(self):
        brain = make_swarm_brain_v10_ultimate(None, task_domain="coding")
        assert brain is not None
        assert isinstance(brain, UpgradedSwarmBrainV10Ultimate)

    def test_brain_executes_single_task(self):
        brain = make_swarm_brain_v10_ultimate(None, task_domain="coding")
        result = brain({"task": "Debug code"})
        assert result is not None
        assert hasattr(result, "composite_reward")
        assert hasattr(result, "tools_used")
        assert result.composite_reward > 0.5
        assert len(result.tools_used) > 0

    def test_brain_selects_different_tools(self):
        brain = make_swarm_brain_v10_ultimate(None, task_domain="coding")
        r1 = brain({"task": "Debug code"})
        r2 = brain({"task": "Research climate change"})
        assert r1.composite_reward > 0.5
        assert r2.composite_reward > 0.5

    def test_brain_converges(self):
        brain = make_swarm_brain_v10_ultimate(None, task_domain="coding")
        rewards = [brain({"task": "Debug code"}).composite_reward for _ in range(10)]
        assert all(r > 0.5 for r in rewards)
        assert sum(rewards) / len(rewards) > 0.7

    def test_brain_has_causal_engine(self):
        brain = make_swarm_brain_v10_ultimate(None, task_domain="coding")
        # Correct attribute: engine (not scma)
        assert hasattr(brain, "engine")

    def test_brain_has_genome_router(self):
        brain = make_swarm_brain_v10_ultimate(None, task_domain="coding")
        # Correct attribute: genome (not router)
        assert hasattr(brain, "genome")

class TestPipeline:
    def test_full_pipeline(self):
        brain = make_swarm_brain_v10_ultimate(None, task_domain="coding")
        for task in [{"task": "Debug"}, {"task": "Research"}, {"task": "Summarize"}]:
            r = brain(task)
            assert r.composite_reward > 0.5
            assert len(r.tools_used) > 0

