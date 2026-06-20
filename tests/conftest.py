from fastapi.testclient import TestClient
import pytest

from swarm_os.app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client

