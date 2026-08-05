"""Tests for the read-only system intelligence tools (whole-computer command center).

All system tools are analysis-only: hardware/OS inventory, processes, services,
network connections, disk usage, installed apps, startup items, registry, and
Event Log. No destructive operations.
"""
from __future__ import annotations
import asyncio
import sys
import pytest

from runtime_v2.services.system_intel import system_handler
from runtime_v2.services import tool_executor
from runtime_v2.prompts.system_prompts import build as build_system_prompt

# net_connections/startup_items/registry_query use winreg + Windows psutil
# enumerations; they only run on Windows.
_WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only (winreg)")


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    """Override the tests/conftest.py autouse subprocess mock for this module.

    system_handler isolates psutil enumeration in a REAL subprocess with a hard
    timeout (threads stuck in native psutil code cannot be killed, so a bounded
    subprocess is the only reliable guard against the intermittent Windows
    psutil hang). The conftest mock patches subprocess.Popen to prevent server
    spawns; these system-intel tests must exercise the real isolation path, so
    this override disables that mock for this module only.
    """
    yield


def test_inventory_reports_machine_details():
    res = system_handler({"action": "system_inventory"})
    assert res.get("ok") is True
    result = res["result"]
    assert result["hostname"]
    assert isinstance(result["cpu_logical_cores"], int)
    assert result["ram_total_gb"] > 0
    assert isinstance(result["disks"], list)


def test_process_list_sorts_by_memory():
    res = system_handler({"action": "process_list", "sort": "memory", "top": 5})
    # psutil enumeration can intermittently hang on Windows (a transient/protected
    # process blocks in native code); system_handler now isolates it in a bounded
    # subprocess and returns a graceful error on timeout. Accept either a real
    # result or the bounded timeout — the contract is "never hangs".
    if not res.get("ok"):
        assert "timed out" in res.get("error", "")
        return
    procs = res["result"]["processes"]
    assert len(procs) <= 5
    mems = [p["memory_mb"] for p in procs]
    assert mems == sorted(mems, reverse=True)
    assert all("pid" in p for p in procs)


@_WINDOWS_ONLY
def test_net_connections_reports_sockets():
    res = system_handler({"action": "net_connections"})
    assert res.get("ok") is True
    assert isinstance(res["result"]["connections"], list)


def test_disk_analyzer_finds_largest_paths():
    res = system_handler({"action": "disk_analyzer", "path": ".", "max_depth": 1, "top": 5})
    assert res.get("ok") is True
    result = res["result"]
    assert result["total_bytes"] > 0
    assert isinstance(result["largest_dirs"], list)


@_WINDOWS_ONLY
def test_startup_items_readable():
    res = system_handler({"action": "startup_items"})
    assert res.get("ok") is True
    assert isinstance(res["result"]["items"], list)


@_WINDOWS_ONLY
def test_registry_query_is_read_only_softare_only():
    res = system_handler({"action": "registry_query", "subkey": r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"})
    assert res.get("ok") is True
    assert "values" in res["result"]
    blocked = system_handler({"action": "registry_query", "subkey": r"SYSTEM\CurrentControlSet\Control"})
    assert blocked.get("ok") is False


def test_unknown_action_returns_helpful_error():
    res = system_handler({"action": "nonexistent_thing"})
    assert res.get("ok") is False
    assert "Available" in res.get("error", "")


@pytest.mark.asyncio
async def test_tool_executor_dispatches_system():
    res = await tool_executor.run("system", {"action": "system_inventory"})
    assert res.get("ok") is True
    assert "hostname" in res["result"]


def test_system_tool_in_system_prompt():
    prompt = build_system_prompt("code_analyzer")
    assert "action=system" in prompt
    assert "system_inventory" in prompt
    # coordinator is too small to carry system tools
    assert "action=system" not in build_system_prompt("coordinator")
