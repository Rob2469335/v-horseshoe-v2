from swarm_os.main import app  # noqa: F401  (app-import regression check)


def test_admin_snapshots(client):
    response = client.get("/api/admin/snapshots")
    assert response.status_code == 200
    data = response.json()
    assert "snapshots" in data
    assert isinstance(data["snapshots"], list)
