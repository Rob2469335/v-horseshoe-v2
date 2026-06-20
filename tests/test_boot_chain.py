def test_boot_chain():
    from organism_console.core.orchestrator import Orchestrator
    assert Orchestrator() is not None
