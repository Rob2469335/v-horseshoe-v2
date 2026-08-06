from swarm_os.main import app  # noqa: F401  (app-import regression check)

def test_run_state(client):
    response = client.get("/api/admin/run-state")
    assert response.status_code == 200
    data = response.json()
    assert "scenario" in data
    assert "latest_snapshot" in data
    assert "snapshot_count" in data
