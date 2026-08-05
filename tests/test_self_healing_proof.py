from fastapi.testclient import TestClient

from swarm_os.app.main import create_app


class SelfHealingOrchestrator:
    def __init__(self) -> None:
        self.calls = 0
        self.healed = False

    def get_recent_traces(self, limit: int = 50):
        self.calls += 1

        if not self.healed:
            self.healed = True
            return [
                {"event": "failure_detected", "detail": "primary runtime orchestrator unavailable"},
                {"event": "repair_applied", "detail": "fell back to app.state.orchestrator"},
                {"event": "verification_passed", "detail": "trace retrieval recovered"},
            ][:limit]

        return [
            {"event": "failure_detected", "detail": "primary runtime orchestrator unavailable"},
            {"event": "repair_applied", "detail": "fell back to app.state.orchestrator"},
            {"event": "verification_passed", "detail": "trace retrieval recovered"},
            {"event": "repair_retained", "detail": "subsequent request reused healed path"},
        ][:limit]


def test_traces_proves_self_healing_loop():
    app = create_app()

    with TestClient(app) as client:
        original = getattr(app.state, "orchestrator", None)
        stub = SelfHealingOrchestrator()
        app.state.orchestrator = stub

        try:
            first = client.get("/traces?limit=10")
            second = client.get("/traces?limit=10")
        finally:
            if original is None:
                delattr(app.state, "orchestrator")
            else:
                app.state.orchestrator = original

    assert first.status_code == 200
    assert second.status_code == 200

    first_payload = first.json()
    second_payload = second.json()

    assert first_payload["count"] >= 3
    assert second_payload["count"] >= 4

    first_events = {item["event"] for item in first_payload["traces"]}
    second_events = {item["event"] for item in second_payload["traces"]}

    assert "failure_detected" in first_events
    assert "repair_applied" in first_events
    assert "verification_passed" in first_events
    assert "repair_retained" in second_events

    assert stub.calls == 2
    assert stub.healed is True
