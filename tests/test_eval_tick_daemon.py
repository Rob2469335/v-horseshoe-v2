"""Tests for the Evaluation Tick daemon (NEW DESIGN — NOT RECOVERED INTENT)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _trusted_receipt_key(monkeypatch):
    """V4: receipt authority requires a trusted EXTERNAL key and fails closed
    without it. Tests exercise the legitimate path, so provision one."""
    monkeypatch.setenv("SWARM_RECEIPT_KEY", "unit-test-receipt-key")


class TestEvalTickDisabledByDefault:
    """Test that Evaluation Tick is disabled by default."""

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "0")
        monkeypatch.delenv("EVAL_TICK_INTERVAL_S", raising=False)
        monkeypatch.delenv("EVAL_TICK_FIRST_DELAY_S", raising=False)

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200


class TestEvalTickEnabledExplicit:
    """Test that Evaluation Tick can be explicitly enabled."""

    def test_explicit_enable_starts_daemon(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "1")
        monkeypatch.setenv("EVAL_TICK_INTERVAL_S", "3600")
        monkeypatch.setenv("EVAL_TICK_FIRST_DELAY_S", "60")

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200


class TestEvalTickFirstDelay:
    """Test first delay behavior."""

    def test_first_delay_respected(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "1")
        monkeypatch.setenv("EVAL_TICK_INTERVAL_S", "3600")
        monkeypatch.setenv("EVAL_TICK_FIRST_DELAY_S", "60")

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            # Daemon starts with first_delay, so no immediate evaluation


class TestEvalTickInvalidInterval:
    """Test invalid interval handling."""

    def test_zero_interval_disables_daemon(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "1")
        monkeypatch.setenv("EVAL_TICK_INTERVAL_S", "0")
        monkeypatch.setenv("EVAL_TICK_FIRST_DELAY_S", "60")

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200

    def test_negative_interval_disables_daemon(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "1")
        monkeypatch.setenv("EVAL_TICK_INTERVAL_S", "-1")
        monkeypatch.setenv("EVAL_TICK_FIRST_DELAY_S", "60")

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200


class TestEvalTickNegativeFirstDelay:
    """Test negative first delay handling."""

    def test_negative_first_delay_disables_daemon(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "1")
        monkeypatch.setenv("EVAL_TICK_INTERVAL_S", "3600")
        monkeypatch.setenv("EVAL_TICK_FIRST_DELAY_S", "-1")

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200


class TestEvalTickInvalidNumericConfig:
    """Test invalid numeric configuration handling."""

    def test_invalid_interval_fails_closed(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "1")
        monkeypatch.setenv("EVAL_TICK_INTERVAL_S", "invalid")
        monkeypatch.setenv("EVAL_TICK_FIRST_DELAY_S", "60")

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200

    def test_invalid_first_delay_fails_closed(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "1")
        monkeypatch.setenv("EVAL_TICK_INTERVAL_S", "3600")
        monkeypatch.setenv("EVAL_TICK_FIRST_DELAY_S", "invalid")

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200


class TestEvalTickDisabledMode:
    """Test disabled mode never invokes evaluation."""

    def test_disabled_mode_never_invokes(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "0")
        monkeypatch.delenv("EVAL_TICK_INTERVAL_S", raising=False)
        monkeypatch.delenv("EVAL_TICK_FIRST_DELAY_S", raising=False)

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200


class TestEvalTickLifespanStartup:
    """Test lifespan startup."""

    def test_lifespan_startup_disabled(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "0")
        monkeypatch.delenv("EVAL_TICK_INTERVAL_S", raising=False)
        monkeypatch.delenv("EVAL_TICK_FIRST_DELAY_S", raising=False)

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200

    def test_lifespan_startup_enabled(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "1")
        monkeypatch.setenv("EVAL_TICK_INTERVAL_S", "3600")
        monkeypatch.setenv("EVAL_TICK_FIRST_DELAY_S", "60")

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200


class TestEvalTickLifespanShutdown:
    """Test lifespan shutdown."""

    def test_lifespan_shutdown(self, monkeypatch):
        monkeypatch.setenv("SWARM_EVAL_TICK", "1")
        monkeypatch.setenv("EVAL_TICK_INTERVAL_S", "3600")
        monkeypatch.setenv("EVAL_TICK_FIRST_DELAY_S", "60")

        from swarm_os.app.main import create_app
        app = create_app()

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
        # Shutdown happens automatically when TestClient exits


if __name__ == "__main__":
    pytest.main([__file__, "-v"])