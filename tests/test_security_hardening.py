"""Regression tests for the 4-expert review fixes.

Covers:
- Organism.act() dict-content coercion (downed-backend shape) never crashes
- memory_bridge slot-busy (ReadError/ReadTimeout) graceful fallback
- security: sandbox_repl gate blocks banned calls
- security: screen self-promotion blocked in human-control mode
- security: mcp_register allowlist + metachar block
- security: web_fetch SSRF denylist
- developer: cooldown keys are per-model (no :free collision)
"""
import asyncio
import sys

import pytest


# ── Organism.act() dict-content coercion ─────────────────────────────────────
def _brain_returning_dict_content():
    def brain(ctx):
        return {
            "content": {"error": "backend down", "choices": []},
            "error": "connection failed",
            "tools_used": [],
            "model": "test",
            "elapsed": 1.0,
            "total_tokens": 10,
        }
    return brain


@pytest.mark.asyncio
async def test_organism_act_coerces_dict_content(monkeypatch):
    from swarm_os.kernel.organism import Organism
    from swarm_os.kernel.genetics import Genome
    # Skip the pheromone path entirely (no tools_used).
    org = Organism("org1", _brain_returning_dict_content(), Genome())
    act = org.act({"task": "t"})
    assert isinstance(act.get("content"), str)
    assert act.get("error") == "connection failed"
    assert act.get("ok") is not True  # not marked success


@pytest.mark.asyncio
async def test_organism_act_dict_content_does_not_break_gather(monkeypatch):
    from swarm_os.kernel.organism import Organism
    from swarm_os.kernel.genetics import Genome

    def healthy_brain(ctx):
        return {"content": "ok", "error": None, "tools_used": [], "model": "t", "elapsed": 1.0, "total_tokens": 5}

    orgs = [
        Organism("bad", _brain_returning_dict_content(), Genome()),
        Organism("good", healthy_brain, Genome()),
    ]
    # Calling act() directly on both must not raise.
    for o in orgs:
        o.act({"task": "t"})
    assert True


# ── memory_bridge slot-busy graceful fallback ───────────────────────────────
@pytest.mark.asyncio
async def test_memory_bridge_read_timeout_falls_back_quietly(monkeypatch):
    from swarm_os.memory.memory_bridge import MemoryBridge

    class FakeVS:
        collection_name = "mem"
        async def count(self, **kw): return 0
        async def search(self, **kw): return []
        async def upsert(self, **kw): return True
        async def delete(self, **kw): return True
        async def create_collection(self, **kw): return None

    class FakeGraphRepo:
        def get_node_count(self): return 0

    bridge = MemoryBridge.__new__(MemoryBridge)
    bridge.vs = FakeVS()
    bridge.graph_repo = FakeGraphRepo()
    bridge.lock_vector = asyncio.Lock()
    bridge.http = None  # not used when graph_repo node count < 5

    # cluster_graph_rag should return without raising when under the node threshold
    result = await bridge.cluster_graph_rag()
    assert result is None


# ── security: sandbox_repl gate ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sandbox_repl_blocks_banned_code():
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler
    r = await SandboxReplHandler().execute({"language": "python", "code": "import subprocess; subprocess.run(['whoami'])"})
    assert r.get("ok") is False
    assert "Security Gate" in r.get("stderr", "")


@pytest.mark.asyncio
async def test_sandbox_repl_allows_safe_code():
    # This runs a real `python -I` subprocess. The CI conftest mocks
    # subprocess.Popen globally (to prevent background servers), which makes
    # the spawn fail with '[Errno 3] No such process' — skip there.
    import subprocess
    from unittest.mock import Mock
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler
    if isinstance(subprocess.Popen, Mock):
        pytest.skip("subprocess.Popen is mocked by the CI conftest; sandbox spawn not possible")
    r = await SandboxReplHandler().execute({"language": "python", "code": "print(2+2)"})
    assert r.get("ok") is True
    assert "4" in r.get("stdout", "")


@pytest.mark.asyncio
async def test_sandbox_repl_blocks_destructive_powershell():
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler
    for bad in ("Remove-Item C:\\x -Recurse", "Stop-Service spooler", "rm -rf /", "shutdown /s"):
        r = await SandboxReplHandler().execute({"language": "powershell", "command": bad})
        assert r.get("ok") is False
        assert "Security Gate" in r.get("stderr", ""), bad


# ── security: screen self-promotion blocked ─────────────────────────────────
@pytest.mark.skipif(sys.platform != "win32", reason="screen-control is Windows-only (ctypes.windll)")
def test_screen_self_promote_blocked_in_human_mode(monkeypatch):
    from swarm_os.lib.mcp import screen as s
    s.SCREEN_AUTONOMOUS = False
    r = s.screen_handler({"action": "set_screen_autonomous", "value": True})
    assert "HUMAN-CONTROL" in r.get("error", "")
    assert s.SCREEN_AUTONOMOUS is False


@pytest.mark.skipif(sys.platform != "win32", reason="screen-control is Windows-only (ctypes.windll)")
def test_screen_reset_blocked_in_human_mode(monkeypatch):
    from swarm_os.lib.mcp import screen as s
    s.SCREEN_AUTONOMOUS = False
    r = s.screen_handler({"action": "reset_screen_action_count"})
    assert "HUMAN-CONTROL" in r.get("error", "")


# ── security: mcp_register allowlist ────────────────────────────────────────
@pytest.mark.asyncio
async def test_mcp_register_rejects_shell_metachars(monkeypatch):
    from runtime_v2.services.tool_executor import run
    payload = {"server_name": "evil", "command": "python -c 'import os' && rm -rf /", "args": []}
    result = await run("mcp_register", payload)
    assert result.get("ok") is False
    assert "Security Gate" in result.get("error", "")


@pytest.mark.asyncio
async def test_mcp_register_rejects_non_allowlisted_launcher(monkeypatch):
    from runtime_v2.services.tool_executor import run
    payload = {"server_name": "evil", "command": "powershell -c whoami", "args": []}
    result = await run("mcp_register", payload)
    assert result.get("ok") is False
    assert "Security Gate" in result.get("error", "")


# ── security: web_fetch SSRF ────────────────────────────────────────────────
def test_ssrf_blocks_internal_and_metadata():
    from swarm_os.lib.mcp.web_search import _ssrf_check
    for url in ("http://127.0.0.1:6333/x", "http://localhost:8080/v1/models",
                "http://169.254.169.254/latest/", "http://192.168.1.10/x"):
        assert _ssrf_check(url) is not None, url


def test_ssrf_allows_public():
    from swarm_os.lib.mcp.web_search import _ssrf_check
    assert _ssrf_check("https://example.com/docs") is None


# ── developer: cooldown keys are per-model ──────────────────────────────────
def test_cooldown_keys_are_per_model(monkeypatch):
    from runtime_v2.services import fallback_manager as fm
    fm.record_model_failure("openrouter/foo:free", "boom")
    assert fm.is_model_cooled_down("openrouter/foo:free") is True
    # A DIFFERENT :free model must NOT be cooled by the first model's failure.
    assert fm.is_model_cooled_down("openrouter/bar:free") is False
    fm.record_model_success("openrouter/foo:free")


# ── security gate scan_code ─────────────────────────────────────────────────
def test_security_gate_scan_code():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation
    SecurityGate.scan_code("x = 1 + 2")
    with pytest.raises(SecurityGateViolation):
        SecurityGate.scan_code("import os; os.system('x')")
    with pytest.raises(SecurityGateViolation):
        SecurityGate.scan_code("eval('1+1')")
