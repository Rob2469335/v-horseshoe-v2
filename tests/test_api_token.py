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
