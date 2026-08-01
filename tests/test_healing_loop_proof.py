import pytest
import time
from unittest.mock import MagicMock
from swarm_os.healing.healing_loop import HealingLoop

def test_healing_loop_detects_and_repairs_qdrant():
    loop = HealingLoop()
    
    # 1. Mock detector to report qdrant failure
    loop.detector.check = MagicMock(return_value={
        "health_score": 50,
        "signals": [
            {"component": "qdrant", "ok": False, "status": "unhealthy"},
            {"component": "memory", "ok": True, "status": "healthy"}
        ]
    })
    
    # 2. First tick warns (escalation: warn once, heal on repeat)
    result = loop.tick()
    assert result["status"] == "transient_warning"
    assert loop.state.consecutive_failures == 1

    # 3. Second tick (same failure) escalates to healing_decision
    result = loop.tick()
    assert result["status"] == "healing_decision"
    assert result["decision"]["mode"] == "approval_required"
    assert loop.state.last_action == "approval_required"
    
    # 4. Subsequent tick should be throttled (cooldown)
    result2 = loop.tick()
    assert result2["status"] == "throttled"
    assert "cooldown_remaining" in result2

def test_healing_loop_stable_when_healthy():
    loop = HealingLoop()
    loop.detector.check = MagicMock(return_value={
        "health_score": 100,
        "signals": []
    })
    
    result = loop.tick()
    assert result["status"] == "stable"
    assert loop.state.last_action is None
