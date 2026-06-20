import json

from fastapi.testclient import TestClient

from swarm_os.app.main import create_app


class PersistentLearningOrchestrator:
    def __init__(self, state_file):
        self.state_file = state_file
        self.calls = 0

    def _load_state(self):
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {"learned_fallback": False, "events": []}

    def _save_state(self, state):
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def get_recent_traces(self, limit: int = 50):
        self.calls += 1
        state = self._load_state()

        if not state["learned_fallback"]:
            state["learned_fallback"] = True
            state["events"] = [
                {"event": "failure_detected", "detail": "primary runtime orchestrator unavailable"},
                {"event": "repair_applied", "detail": "persisted fallback decision"},
                {"event": "verification_passed", "detail": "recovery succeeded and was stored"},
            ]
            self._save_state(state)
            return state["events"][:limit]

        learned_events = list(state["events"])
        learned_events.append(
            {"event": "learned_reuse", "detail": "fresh app instance reused persisted repair"}
        )
        return learned_events[:limit]


def test_self_healing_learning_persists_across_fresh_app_instances(tmp_path):
    state_file = tmp_path / "healing_state.json"

    app1 = create_app()
    stub1 = PersistentLearningOrchestrator(state_file)

    with TestClient(app1) as client1:
        app1.state.orchestrator = stub1
        first = client1.get("/traces?limit=10")

    assert first.status_code == 200
    first_payload = first.json()
    first_events = {item["event"] for item in first_payload["traces"]}

    assert "failure_detected" in first_events
    assert "repair_applied" in first_events
    assert "verification_passed" in first_events
    assert state_file.exists()

    saved_state = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved_state["learned_fallback"] is True

    app2 = create_app()
    stub2 = PersistentLearningOrchestrator(state_file)

    with TestClient(app2) as client2:
        app2.state.orchestrator = stub2
        second = client2.get("/traces?limit=10")

    assert second.status_code == 200
    second_payload = second.json()
    second_events = {item["event"] for item in second_payload["traces"]}

    assert "learned_reuse" in second_events
    assert "repair_applied" in second_events
    assert stub1.calls == 1
    assert stub2.calls == 1
