from unittest.mock import patch

@patch("qdrant_client.QdrantClient")
def test_boot_chain(mock_qdrant):
    from swarm_os.core.orchestrator import Orchestrator
    assert Orchestrator() is not None
