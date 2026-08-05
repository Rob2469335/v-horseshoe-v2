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
    routes = get_route_map()
    route_paths = [r[0] for r in routes]
    
    assert "/health" in route_paths
    assert "/api/admin/status" in route_paths
    assert "/api/admin/run-state" in route_paths
    assert "/api/admin/dashboard" in route_paths
    assert "/api/admin/generation" in route_paths
    assert "/tools" in route_paths
    assert "/tools/execute" in route_paths
    
    # Check specific methods
    health_methods = next(methods for path, methods in routes if path == "/health")
    assert "GET" in health_methods
    
    execute_methods = next(methods for path, methods in routes if path == "/tools/execute")
    assert "POST" in execute_methods
    
    # New alignment route assertions
    assert "/swarm/v10/stream" in route_paths
    assert "/traces/summary" in route_paths
    assert "/healing/evaluate" in route_paths
    
    evaluate_methods = {m for path, methods in routes if path == "/healing/evaluate" for m in methods}
    assert "GET" in evaluate_methods
    assert "POST" in evaluate_methods
