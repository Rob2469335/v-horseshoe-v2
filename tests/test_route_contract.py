from swarm_os.app.main import create_app

def get_route_map():
    app = create_app()
    
    def _collect(router_or_app, prefix=""):
        rows = []
        routes = getattr(router_or_app, "routes", [])
        for route in routes:
            if hasattr(route, "path"):
                methods = sorted(list(getattr(route, "methods", []) or []))
                rows.append((prefix + route.path, tuple(methods)))
            elif type(route).__name__ == "_IncludedRouter":
                sub_prefix = route.include_context.prefix
                rows.extend(_collect(route.original_router, prefix + sub_prefix))
        return rows

    return _collect(app)

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
