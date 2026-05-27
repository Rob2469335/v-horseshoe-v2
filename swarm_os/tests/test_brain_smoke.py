def test_brain_module_imports():
    import swarm_os.kernel.brain  # noqa: F401


def test_orchestrator_evolve_smoke():
    from swarm_os.services.orchestrator import Orchestrator
    o = Orchestrator()
    o.evolve()
