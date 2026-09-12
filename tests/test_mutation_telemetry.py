import pytest
from unittest.mock import patch, AsyncMock
from swarm_os.services.genetic_mutation_loop import run_genetic_mutation
from swarm_os.services.security_gate import SecurityGateViolation

@pytest.mark.asyncio
async def test_mutation_loop_security_violation_telemetry_failure_is_logged(caplog, tmp_path):
    """When a SecurityGateViolation learning record fails to write (e.g. Qdrant
    timeout or disk full), the mutation loop must LOG the telemetry loss rather
    than silently swallowing it with a bare 'except Exception: pass'."""
    import logging

    caplog.set_level(logging.WARNING)

    # 1. Force the model to return code that will trigger a SecurityGateViolation
    async def mock_acompletion(*args, **kwargs):
        class Resp:
            class Choice:
                class Message:
                    content = '```python\nimport os\nos.system("rm -rf /")\n```'
                message = Message()
            choices = [Choice()]
        return Resp()

    # 2. Mock memory_bridge._flush to fail
    async def failing_flush():
        raise RuntimeError("Mocked Qdrant/disk timeout")

    # Better MockBridge to avoid inspect hacks:
    flush_calls = [0]
    class MockBridge:
        def _add(self, item):
            pass
        async def _flush(self):
            flush_calls[0] += 1
            if flush_calls[0] <= 3:
                raise RuntimeError("Mocked Qdrant/disk timeout")
        async def query_routing_hint(self, *args, **kwargs):
            return {}
        async def get_memory_context(self, *args, **kwargs):
            return []
        async def close(self):
            pass

    dummy_file = tmp_path / "dummy.py"
    dummy_file.write_text("def dummy():\n    pass")

    with patch("swarm_os.services.genetic_mutation_loop.acompletion", new=mock_acompletion), \
         patch("swarm_os.services.genetic_mutation_loop.MemoryBridge", return_value=MockBridge()), \
         patch("runtime_v2.services.fallback_manager.get_live_fallbacks", new_callable=AsyncMock, return_value=[{"model": "dummy", "provider": "dummy"}]), \
         patch("swarm_os.services.security_gate.SecurityGate.scan_code", side_effect=SecurityGateViolation("Banned pattern")):
         
        # Run the loop. We limit MAX_RETRIES to 1 so it finishes fast.
        await run_genetic_mutation(target_file_path=str(dummy_file), target_func="dummy")

    # Assert the telemetry failure was logged
    log_messages = [record.message for record in caplog.records]
    assert any("failed to persist SecurityGateViolation telemetry" in msg for msg in log_messages), f"Telemetry failure was swallowed and not logged! Logs: {log_messages}"
