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
    
    # 2. First tick should trigger healing
    result = loop.tick()
    
    assert result["status"] == "healing_executed"
    assert result["result"]["action"] == "restart_vector_layer"
    assert loop.state.last_action == "restart_vector_layer"
    
    # 3. Subsequent tick should be throttled (cooldown)
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
