"""Tests that the `/tools` endpoint reports the LIVE external MCP tools, not just
the ~22 built-in agent tools.

The startup script's STEP 4.5 banner and the web console both read `/tools`; if
it under-counts, the swarm looks like the newly-added MCP servers (github,
s2_scholar, seq_thinking, code_review, ...) are missing when they are actually
loaded (verified live: 79 external tools). The endpoint must merge the loaded
MCP tools via the non-spawning accessor.
"""
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.api import routes
from swarm_os.api.dependencies import runtime_dep


def _app():
    app = FastAPI()
    fake_runtime = MagicMock()
    fake_runtime.agent_runtime = None
    fake_runtime.cache = None
    app.state.runtime = fake_runtime
    app.dependency_overrides[runtime_dep] = lambda: fake_runtime
    app.include_router(routes.router)
    return app


def test_tools_reports_external_mcp_tools(monkeypatch):
    """When external MCP tools are loaded, /tools must include them (mcp:<server>:<name>)
    in both capabilities and the count — the startup banner reads this."""
    def _fake_mcp_tools():
        return [
            {"server": "github", "name": "get_file_contents", "description": "x"},
            {"server": "s2_scholar", "name": "semantic_scholar_search_papers", "description": "y"},
            {"server": "code_review", "name": "scan_diff", "description": "z"},
            {"server": "seq_thinking", "name": "sequentialthinking", "description": "w"},
        ]

    monkeypatch.setattr(
        "runtime_v2.services.tool_executor.get_loaded_mcp_tools", _fake_mcp_tools
    )
    app = _app()
    with TestClient(app) as c:
        resp = c.get("/tools")
    assert resp.status_code == 200
    data = resp.json()
    mcp_names = [n for n in data["capabilities"] if n.startswith("mcp:")]
    assert len(mcp_names) == 4, f"expected the 4 loaded MCP tools, got {mcp_names}"
    assert "mcp:github:get_file_contents" in mcp_names
    assert "mcp:s2_scholar:semantic_scholar_search_papers" in mcp_names
    assert "mcp:code_review:scan_diff" in mcp_names
    assert "mcp:seq_thinking:sequentialthinking" in mcp_names
    assert data["count"] == len(data["capabilities"])


def test_tools_with_no_mcp_returns_builtin_only(monkeypatch):
    """With no MCP tools loaded (manager still initializing / failed), /tools
    must still return the built-in agent tools and a consistent count — never
    raise and never spawn processes."""
    def _fake_mcp_tools():
        return []

    monkeypatch.setattr(
        "runtime_v2.services.tool_executor.get_loaded_mcp_tools", _fake_mcp_tools
    )
    app = _app()
    with TestClient(app) as c:
        resp = c.get("/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == len(data["capabilities"])
    assert all(not n.startswith("mcp:") for n in data["capabilities"])


def test_get_loaded_mcp_tools_never_spawns():
    """get_loaded_mcp_tools must be non-spawning: returns [] when the manager
    is None, and returns the cached tools when initialized — it must never call
    start()/spawn npx/uvx."""
    import runtime_v2.services.tool_executor as te

    te._mcp_manager = None
    assert te.get_loaded_mcp_tools() == []

    mock_mgr = MagicMock()
    mock_mgr.cached_tools = [{"server": "s", "name": "t", "description": "d"}]
    te._mcp_manager = mock_mgr
    try:
        tools = te.get_loaded_mcp_tools()
        assert len(tools) == 1
        mock_mgr.start.assert_not_called()
    finally:
        te._mcp_manager = None
