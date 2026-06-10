from fastapi.testclient import TestClient
import pytest

from swarm_os.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
