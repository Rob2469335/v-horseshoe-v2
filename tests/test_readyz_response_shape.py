"""Verify /readyz response contract against the actual implementation.

Derived from swarm_os/app/main.py:296-314:
    checks = {
        "runtime_started": getattr(runtime, "orchestrator", None) is not None,
        "llamacpp_reachable": ollama_reachable,
        "models_loaded": len(installed_models) > 0,
        "health_score_ok": report["health_score"] >= 60,
    }
    ready = all(checks.values())
    return {
        "status": "ready" if ready else "not-ready",
        "ready": ready,
        "checks": checks,
        "health_score": report["health_score"],
        "overall": report["overall"],
    }

The endpoint returns HTTP 200 in ALL cases (healthy or degraded) — it never
returns 503. The readiness decision is in the response body, not the status code.
"""
from __future__ import annotations


class TestReadyzHealthy:
    def test_status_code_always_200(self, client):
        r = client.get("/readyz")
        assert r.status_code == 200

    def test_response_has_required_fields(self, client):
        r = client.get("/readyz")
        body = r.json()
        assert "ready" in body
        assert "status" in body
        assert "checks" in body
        assert "health_score" in body
        assert "overall" in body

    def test_ready_field_is_boolean(self, client):
        r = client.get("/readyz")
        assert isinstance(r.json()["ready"], bool)

    def test_status_matches_ready(self, client):
        r = client.get("/readyz")
        body = r.json()
        assert body["status"] == ("ready" if body["ready"] else "not-ready")

    def test_checks_has_all_four_keys(self, client):
        r = client.get("/readyz")
        checks = r.json()["checks"]
        assert set(checks.keys()) == {
            "runtime_started",
            "llamacpp_reachable",
            "models_loaded",
            "health_score_ok",
        }

    def test_checks_values_are_boolean(self, client):
        r = client.get("/readyz")
        for v in r.json()["checks"].values():
            assert isinstance(v, bool)

    def test_health_score_is_numeric(self, client):
        r = client.get("/readyz")
        score = r.json()["health_score"]
        assert isinstance(score, (int, float))

    def test_overall_is_string(self, client):
        r = client.get("/readyz")
        assert isinstance(r.json()["overall"], str)

    def test_ready_implies_all_checks_true(self, client):
        """If ready=True, every individual check must be True."""
        r = client.get("/readyz")
        body = r.json()
        if body["ready"]:
            assert all(body["checks"].values())

    def test_not_ready_implies_at_least_one_check_false(self, client):
        """If ready=False, at least one check must be False."""
        r = client.get("/readyz")
        body = r.json()
        if not body["ready"]:
            assert not all(body["checks"].values())


class TestReadyzDegraded:
    def test_endpoint_never_returns_503(self, client):
        """The readyz endpoint always returns 200 — readiness is in the body."""
        r = client.get("/readyz")
        assert r.status_code == 200

    def test_degraded_state_sets_not_ready_string(self, client):
        """When ready=False, status must be 'not-ready'."""
        r = client.get("/readyz")
        body = r.json()
        if not body["ready"]:
            assert body["status"] == "not-ready"
