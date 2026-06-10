from swarm_os.app.main import create_app

def get_route_map():
    app = create_app()
    rows = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = sorted(list(getattr(route, "methods", []) or []))
        rows.append((path, tuple(methods)))
    return rows

def test_route_contract_minimum():
    route_map = dict(get_route_map())
    assert "/health" in route_map
    assert "/api/admin/status" in route_map
    assert "/api/admin/run-state" in route_map
    assert "/api/admin/dashboard" in route_map
    assert "/api/admin/generation" in route_map
    assert "/tools" in route_map
    assert "/tools/execute" in route_map
    assert "GET" in route_map["/health"]
    assert "POST" in route_map["/tools/execute"]
