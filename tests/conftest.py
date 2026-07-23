from fastapi.testclient import TestClient
import pytest

from swarm_os.app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client

from unittest.mock import patch
from qdrant_client import QdrantClient

@pytest.fixture(autouse=True)
def global_qdrant_mock():
    # Intercept any QdrantClient instantiation and force it to be an in-memory client.
    # This prevents the test suite from requiring a live local Qdrant server.
    def mock_init(*args, **kwargs):
        return QdrantClient(":memory:")

    with patch("swarm_os.services.vector_store.QdrantClient", side_effect=mock_init):
        yield

@pytest.fixture(autouse=True)
def global_subprocess_mock():
    # Prevent tests from spawning actual background servers (like uvicorn or ollama) 
    # which leads to PytestUnhandledThreadExceptionWarning and zombie processes.
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.communicate.return_value = (b"", b"")
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.pid = 99999
        yield mock_popen
