from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.api.api_features import router as features_router


def test_healing_overview_returns_expected_shape():
    app = FastAPI()
    app.include_router(features_router)
    client = TestClient(app)

    response = client.get("/features/healing-overview")

    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "ok"
    assert "overview_status" in body
    assert "readiness" in body
    assert "approvals" in body
    assert "metrics" in body
    assert "audit" in body
    assert "escalations" in body
    assert "components" in body
    assert "runbooks" in body
