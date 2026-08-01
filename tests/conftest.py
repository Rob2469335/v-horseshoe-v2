import os
import ssl
try:
    ssl._create_default_https_context = ssl._create_unverified_context
    ssl.create_default_context = ssl._create_unverified_context
except AttributeError:
    pass
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import swarm_os.bootstrap
from fastapi.testclient import TestClient
import pytest

from swarm_os.app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client

from unittest.mock import patch
from qdrant_client import AsyncQdrantClient

@pytest.fixture(autouse=True)
def global_qdrant_mock():
    # Intercept any AsyncQdrantClient instantiation and force it to be an in-memory client.
    # This prevents the test suite from requiring a live local Qdrant server.
    def mock_init(*args, **kwargs):
        return AsyncQdrantClient(":memory:")

    with patch("swarm_os.services.vector_store.AsyncQdrantClient", side_effect=mock_init):
        with patch("swarm_os.services.reflection_loop.AsyncQdrantClient", side_effect=mock_init, create=True):
            with patch("swarm_os.services.tool_registry.AsyncQdrantClient", side_effect=mock_init, create=True):
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
