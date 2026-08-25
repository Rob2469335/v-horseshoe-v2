import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from swarm_os.healing.recovery_primitives import (
    kill_process_by_name,
    clean_directory,
    restart_service,
    RECOVERY_PRIMITIVES,
)
from swarm_os.healing.recovery_engine import llm_guided_recovery


def test_recovery_primitives_registry_complete():
    assert "kill_process_by_port" in RECOVERY_PRIMITIVES
    assert "kill_process_by_name" in RECOVERY_PRIMITIVES
    assert "clean_directory" in RECOVERY_PRIMITIVES
    assert "restart_service" in RECOVERY_PRIMITIVES


def test_kill_process_by_name_rejects_system_processes():
    res = kill_process_by_name("svchost.exe")
    assert res["ok"] is False
    assert "targets protected system process" in res["error"]

    res_exp = kill_process_by_name("explorer.exe")
    assert res_exp["ok"] is False
    assert "targets protected system process" in res_exp["error"]


def test_clean_directory_path_traversal_guard(tmp_path):
    res = clean_directory("../../outside", [".tmp"])
    assert res["ok"] is False
    assert "escapes project root" in res["error"]


def test_clean_directory_valid(tmp_path):
    # Test cleaning files inside project directory
    target_rel = "tests"
    res = clean_directory(target_rel, [".nonexistent_ext"], max_age_hours=0)
    assert res["ok"] is True
    assert res["removed_count"] == 0


def test_restart_service_allowlist_rejection():
    res = restart_service("arbitrary_untrusted_service")
    assert res["ok"] is False
    assert "not in allowed service list" in res["error"]


@pytest.mark.asyncio
async def test_llm_guided_recovery_structured_execution():
    anomaly = {"error": "Stale temp files filling disk", "type": "disk_space"}

    mock_choice = MagicMock()
    mock_choice.message.content = '```json\n{"primitive": "clean_directory", "args": {"target_dir": "tests", "extensions": [".tmp"], "max_age_hours": 24}}\n```'
    mock_res = MagicMock()
    mock_res.choices = [mock_choice]

    with (
        patch("swarm_os.healing.recovery_engine.acompletion", return_value=mock_res),
        patch("swarm_os.healing.recovery_engine.MemoryBridge") as mock_mb_cls,
        patch("swarm_os.healing.recovery_engine._record_to_agents_md"),
    ):
        mock_mb = MagicMock()
        mock_mb.get_memory_context = AsyncMock(return_value="")
        mock_mb._add = MagicMock()
        mock_mb._flush = AsyncMock(return_value=None)
        mock_mb_cls.return_value = mock_mb

        outcome = await llm_guided_recovery(anomaly)
        assert outcome["ok"] is True
        assert outcome["action"] == "primitive:clean_directory"
        assert outcome["result"]["ok"] is True
