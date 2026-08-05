from fastapi.testclient import TestClient
from swarm_os.app.main import create_app

def make_client():
    app = create_app()
    return TestClient(app)

def get_paths(app_or_router):
    paths = set()
    routes = getattr(app_or_router, "routes", [])
    for route in routes:
        if hasattr(route, "path"):
            paths.add(route.path)
        elif type(route).__name__ == "_IncludedRouter":
            prefix = route.include_context.prefix
            nested_paths = get_paths(route.original_router)
            for np in nested_paths:
                paths.add(prefix + np)
    return paths

def test_app_boots():
    app = create_app()
    assert app is not None
    assert app.title == "Swarm OS"

def test_health_ok():
    with make_client() as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

def test_expected_core_routes_are_registered():
    app = create_app()
    paths = get_paths(app)
    assert "/health" in paths
    assert "/api/admin/dashboard" in paths

def test_optional_routes_if_present_respond():
    app = create_app()
    paths = get_paths(app)

    checks = {
        "/readyz": ["status", "ready", "checks", "health_score"],
        "/api/admin/explorer": ["scenario", "latest_snapshot", "current_run"],
        "/api/admin/generation": ["scenario", "latest_snapshot", "current_run", "population"],
        "/api/admin/run-state": ["scenario", "latest_snapshot", "snapshot_count"],
    }

    with TestClient(app) as client:
        for route, keys in checks.items():
            assert route in paths, f"{route} is not registered"
            r = client.get(route)
            assert r.status_code == 200, route
            data = r.json()
            for key in keys:
                assert key in data, f"{route} missing {key}"
