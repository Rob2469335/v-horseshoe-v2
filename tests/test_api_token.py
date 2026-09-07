"""Opt-in loopback API token (SWARM_API_TOKEN) tests."""

import os
import pytest


@pytest.fixture
def token_app():
    os.environ["SWARM_API_TOKEN"] = "test-token-123"
    try:
        from swarm_os.app.main import create_app

        app = create_app()
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            yield client
    finally:
        os.environ.pop("SWARM_API_TOKEN", None)


def test_health_exempt_from_token(token_app):
    r = token_app.get("/health")
    assert r.status_code == 200


def test_protected_route_rejects_missing_token(token_app):
    r = token_app.get("/agents")
    assert r.status_code == 401


def test_protected_route_accepts_token(token_app):
    r = token_app.get("/agents", headers={"Authorization": "Bearer test-token-123"})
    # /agents may 200 or 5xx depending on runtime state, but must NOT be 401.
    assert r.status_code != 401


def test_protected_route_rejects_wrong_token(token_app):
    r = token_app.get("/agents", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_cli_auth_headers():
    from organism_console.api_client import _auth_headers

    os.environ["SWARM_API_TOKEN"] = "cli-token"
    try:
        assert _auth_headers() == {"Authorization": "Bearer cli-token"}
    finally:
        os.environ.pop("SWARM_API_TOKEN", None)
    assert _auth_headers() == {}


# ---------------------------------------------------------------------------
# Route-level auth gates (2026-09). verify_api_key now guards the sensitive
# surfaces: /control/*, /admin/*, /tools/execute, /generate, /intel/*,
# /healing-approvals/*. Default fail-open (no key -> all pass, preserving the
# loopback single-user dev posture); with SWARM_API_KEY set, the sensitive
# routes require X-API-Key OR Authorization: Bearer (the CLI's scheme), while
# open read routes (/health, /status) stay open.
# ---------------------------------------------------------------------------


@pytest.fixture
def gated_app(monkeypatch):
    monkeypatch.setenv("SWARM_API_KEY", "sekrit-123")
    from swarm_os.app.main import create_app

    app = create_app()
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        yield client


@pytest.fixture
def open_app(monkeypatch):
    monkeypatch.delenv("SWARM_API_KEY", raising=False)
    monkeypatch.delenv("SWARM_API_TOKEN", raising=False)
    from swarm_os.app.main import create_app

    app = create_app()
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        yield client


SENSITIVE = [
    ("GET", "/admin/replay"),
    ("POST", "/control/permissions/grant"),
    ("POST", "/generate"),
    ("POST", "/tools/execute"),
    ("GET", "/features/healing-approvals"),
    ("POST", "/features/intel/run"),
]
OPEN = [
    ("GET", "/health"),
    ("GET", "/status"),
    ("GET", "/tools"),
]


@pytest.mark.parametrize("method,path", SENSITIVE)
def test_sensitive_route_requires_key_when_set(gated_app, method, path):
    r = gated_app.request(method, path, json={} if method == "POST" else None)
    assert r.status_code == 401, f"{method} {path} should be gated, got {r.status_code}"


@pytest.mark.parametrize("method,path", OPEN)
def test_open_route_not_gated_when_set(gated_app, method, path):
    r = gated_app.get(path)
    assert r.status_code != 401, f"{method} {path} should stay open"


def test_sensitive_route_accepts_x_api_key(gated_app):
    r = gated_app.get(
        "/admin/replay", headers={"X-API-Key": "sekrit-123"}
    )
    assert r.status_code != 401, "correct X-API-Key should pass"


def test_sensitive_route_accepts_bearer_cli_scheme(gated_app):
    r = gated_app.get(
        "/admin/replay", headers={"Authorization": "Bearer sekrit-123"}
    )
    assert r.status_code != 401, "CLI Bearer scheme should pass"


@pytest.mark.parametrize("method,path", SENSITIVE)
def test_sensitive_route_fail_open_with_no_key(open_app, method, path):
    r = open_app.request(method, path, json={} if method == "POST" else None)
    assert r.status_code != 401, f"{method} {path} should fail open with no key"
