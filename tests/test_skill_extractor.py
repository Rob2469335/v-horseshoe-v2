import time
from swarm_os.healing.learner import Learner
from swarm_os.healing.governor_models import FailureRecord
from swarm_os.healing.skill_extractor import SkillExtractor


def make_failure(incident_id, action_name):
    return FailureRecord(
        incident_id=incident_id,
        symptom={"component": "worker"},
        root_cause=None,
        hypotheses=[],
        repair_attempts=[{"action": action_name, "reason": "auto"}],
        successful_fix={"action": action_name, "result": "success"},
        confidence=0.9,
        outcome="SUCCESS",
        service="worker",
        environment={},
        metrics_before={},
        metrics_after={},
        timestamp=time.time(),
    )


def test_skill_extractor_creates_skill(tmp_path):
    timeline = tmp_path / "timeline.json"
    learner = Learner(learning_service=None, timeline_path=str(timeline))
    # create repeated successful failures
    for i in range(4):
        fr = make_failure(f"inc-{i}", "restart")
        learner.persist_failure(fr)
    se = SkillExtractor(learner=learner, min_occurrences=3, lookback=10)
    skills = se.extract()
    assert len(skills) >= 1
    skill = skills[0]
    assert "restart" in ",".join([a.get("action") for a in skill.repair_sequence])
