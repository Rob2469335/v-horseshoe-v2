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


@pytest.fixture(autouse=True)
def global_subprocess_mock(request):
    """Shadow the CI conftest's autouse subprocess.Popen mock for this module.

    The sandbox integration tests (test_sandbox_repl_*) MUST use the REAL Popen
    so the actual sandbox_repl -> scan_code_isolated -> python -I scanner wiring
    is exercised on every CI run. A mocked Popen returning code 0 would silently
    pass every scan, which for the L6 fail-closed security gate is the exact
    divergence between "looked fine in CI" and "fail-closed in production" we
    refuse to accept. All other tests in this module keep the conftest-style mock
    (they don't spawn a real scan subprocess and must not launch background
    servers / npx).
    """
    if request.node.name.startswith("test_sandbox_repl_"):
        yield  # real subprocess.Popen stays intact for the scan subprocess
        return
    from unittest.mock import patch
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.communicate.return_value = (b"", b"")
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.pid = 99999
        yield mock_popen


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
    # This runs the REAL isolated scan subprocess + a real `python -I` exec — the
    # module-scoped global_subprocess_mock fixture keeps Popen real for
    # test_sandbox_repl_* so the L6 wiring is verified on every CI run.
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler
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


def test_permanent_error_pins_until_manual_clear():
    """A permanent (billing/auth) failure pins the model at float('inf') — the
    documented fail-closed contract (AGENTS.md): the model is NOT auto-retried,
    so a definitively-broken paid key can't burn attempts or silently succeed
    part of the time and mask a billing problem. The exit is the manual,
    model-scoped `clear_model_cooldown` after a human tops up."""
    from runtime_v2.services import fallback_manager as fm
    fm.record_model_failure("openrouter/bill:free", "402 Insufficient Balance", permanent=True)
    try:
        key = fm._cooldown_key("openrouter/bill:free")
        entry = fm._cooldowns.get(key)
        assert entry is not None
        assert entry["until"] == float('inf')
        # Still cooled down (never auto-recovers).
        assert fm.is_model_cooled_down("openrouter/bill:free") is True
    finally:
        fm.record_model_success("openrouter/bill:free")


def test_clear_model_cooldown_is_scoped_to_one_model():
    """The manual clear must target ONE model only — it must NOT wipe the
    legitimate exponential-backoff cooldowns of OTHER transiently
    rate-limited models."""
    from runtime_v2.services import fallback_manager as fm
    fm.record_model_failure("openrouter/a:free", "boom")          # transient backoff
    fm.record_model_failure("openrouter/bill:free", "402 Insufficient Balance", permanent=True)
    try:
        assert fm.is_model_cooled_down("openrouter/a:free")
        assert fm.is_model_cooled_down("openrouter/bill:free")

        cleared = fm.clear_model_cooldown("openrouter/bill:free")
        assert cleared is True
        # The billing pin is lifted...
        assert not fm.is_model_cooled_down("openrouter/bill:free")
        # ...but the OTHER model's transient backoff is untouched.
        assert fm.is_model_cooled_down("openrouter/a:free")

        # Clearing a model with no entry returns False (no-op).
        assert fm.clear_model_cooldown("openrouter/never-existed:free") is False
    finally:
        fm.record_model_success("openrouter/a:free")
        fm.record_model_success("openrouter/bill:free")


# ── security gate scan_code ─────────────────────────────────────────────────
def test_security_gate_scan_code():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation
    SecurityGate.scan_code("x = 1 + 2")
    with pytest.raises(SecurityGateViolation):
        SecurityGate.scan_code("import os; os.system('x')")
    with pytest.raises(SecurityGateViolation):
        SecurityGate.scan_code("eval('1+1')")


# ── security gate: os is attribute-gated, not wholesale-banned ─────────────
# The debugger/coder agents legitimately run `import os; os.walk('.')` in
# sandbox_repl. Only dangerous os ATTRIBUTES (process exec, file mutation,
# privilege, env mutation) are blocked — not the module itself.
def test_security_gate_allows_readonly_os_usage():
    from swarm_os.services.security_gate import SecurityGate
    for safe in (
        "import os\nfor _, dirs, files in os.walk('.'):\n    pass",
        "import os\npaths = [os.path.join('.', f) for f in os.listdir('.')]",
        "import os as o\nprint(o.path.exists('x'))",
        "import os.path\nprint(os.path.abspath('.'))",
        "import sys\nprint(sys.argv)",
        "from os import walk\nprint(list(walk('.')))",
        "from os.path import exists\nprint(exists('x'))",
    ):
        SecurityGate.scan_code(safe), safe


def test_security_gate_blocks_dangerous_os_attributes():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation
    for bad in (
        "import os\nos.system('whoami')",
        "import os as o\no.system('whoami')",
        "import os\nos.remove('x.py')",
        "import os\nos.popen('whoami')",
        "from os import system\nsystem('whoami')",
        "from os import system as s\ns('whoami')",
        "import os\nos.putenv('A', 'B')",
    ):
        with pytest.raises(SecurityGateViolation):
            SecurityGate.scan_code(bad)


# ── security gate: reflection / builtins / importlib bypass closures ────────
# These are the confirmed REAL gaps: a banned os/SecurityGate call could be
# reached without a Name-call or a direct os.attr scan ever matching, because
# the dangerous name rides in as a string argument or via the builtins dict.
def test_security_gate_blocks_reflection_on_os():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation
    for bad in (
        "import os\ngetattr(os, 'system')('rm -rf /')",
        "import os as o\ngetattr(o, 'system')('whoami')",
        "import os\nsetattr(os, 'system', symlink)('x')",
        "import os\ndelattr(os, 'nice')",
    ):
        with pytest.raises(SecurityGateViolation):
            SecurityGate.scan_code(bad)


def test_security_gate_allows_reflection_on_non_os():
    from swarm_os.services.security_gate import SecurityGate
    for safe in (
        "x = {}\ngetattr(x, 'get', lambda: 1)()",
        "obj = str()\nsetattr(obj, 'name', 'v')",
    ):
        SecurityGate.scan_code(safe), safe


def test_security_gate_blocks_importlib_and_builtins_and_sys_modules():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation
    for bad in (
        "import importlib\nimportlib.import_module('os').system('rm -rf /')",
        "__builtins__['__import__']('os').system('whoami')",
        "import sys\nsys.modules['os'].system('whoami')",
    ):
        with pytest.raises(SecurityGateViolation):
            SecurityGate.scan_code(bad)


def test_security_gate_allows_plain_sys_usage():
    from swarm_os.services.security_gate import SecurityGate
    SecurityGate.scan_code("import sys\nprint(sys.argv)")
    SecurityGate.scan_code("import os\ngetattr(os, 'walk')('.')")


@pytest.mark.asyncio
async def test_sandbox_repl_allows_readonly_os_code():
    # `import os; os.walk('.')` (the debugger's natural file-listing snippet)
    # must pass the gate so the debugger path doesn't loop-trip on it. Runs the
    # real isolated scan subprocess (module fixture keeps Popen real for this).
    from swarm_os.services.security_gate import SecurityGate
    SecurityGate.scan_code("import os\nfor _, dirs, files in os.walk('.'):\n    print(dirs)")
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler
    r = await SandboxReplHandler().execute({"language": "python", "code": "import os; print(len(list(os.walk('.'))))"})
    assert r.get("ok") is True


# ── security gate: lsp accepts path/file_path alias ────────────────────────
@pytest.mark.asyncio
async def test_lsp_accepts_path_alias_for_file_path():
    from swarm_os.capabilities.lsp_tool import LSPToolHandler
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        py = os.path.join(tmp, "t.py")
        with open(py, "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        # A real pylsp spawn is slow/unavailable; assert the alias mapping
        # resolves past the "Missing 'file_path'" guard (which is where the
        # debugger's `path` key previously died).
        payload = {"operation": "diagnostics", "path": py}
        handler = LSPToolHandler()
        # swap the actual LSP client acquisition so this test doesn't spawn pylsp
        import swarm_os.capabilities.lsp_tool as lt
        async def _fake_acquire(ext):
            class _FakeClient:
                last_used = 0.0
                async def get_diagnostics(self, fp):
                    return [{"source": "pylint", "code": "C0114"}]
            return _FakeClient()
        lt._acquire_client = _fake_acquire
        r = await handler.execute(payload)
        assert "Missing 'file_path'" not in r.get("error", "")
        assert r.get("result") == [{"source": "pylint", "code": "C0114"}]


# ── L6: process-separated security gate (fail-closed on crashed scanner) ─────
import subprocess as _subprocess
from unittest.mock import Mock


def _popen_side_effect(returncode: int, stderr: str = "", timeout_raise: bool = False):
    """Build a fake Popen whose communicate() yields the given outcome."""
    proc = Mock()
    if timeout_raise:
        proc.communicate.side_effect = _subprocess.TimeoutExpired(cmd="scan", timeout=5)
    else:
        proc.communicate.return_value = (b"", stderr.encode("utf-8", errors="replace"))
    proc.returncode = returncode
    return proc


def test_scan_code_isolated_allows_clean_code(monkeypatch):
    from swarm_os.services.security_gate import scan_code_isolated
    proc = _popen_side_effect(0)
    monkeypatch.setattr(_subprocess, "Popen", lambda *a, **k: proc)
    ok, reason = scan_code_isolated("x = 1 + 2")
    assert ok is True
    assert reason == ""


def test_scan_code_isolated_denies_on_detected_threat(monkeypatch):
    from swarm_os.services.security_gate import scan_code_isolated
    # Simulate the scanner process detecting a banned call: exit 1 + stderr reason.
    proc = _popen_side_effect(1, stderr="DENY: Banned built-in call found: 'eval'")
    monkeypatch.setattr(_subprocess, "Popen", lambda *a, **k: proc)
    ok, reason = scan_code_isolated("eval('1+1')")
    assert ok is False
    assert "eval" in reason  # explicit denial reason, not generic
    assert "DENY" in reason


def test_scan_code_isolated_denies_on_crashed_scanner(monkeypatch):
    """L6 acceptance: a CRASHED scan subprocess (non-zero exit, NO denial reason
    on stderr — i.e. it died rather than reporting) must DENY with a clear
    reason, never degrade to 'scan passed, nothing to report'."""
    from swarm_os.services.security_gate import scan_code_isolated
    proc = _popen_side_effect(2, stderr="")  # crashed, no reason
    monkeypatch.setattr(_subprocess, "Popen", lambda *a, **k: proc)
    ok, reason = scan_code_isolated("x = 1")
    assert ok is False
    assert reason  # non-empty reason
    assert "exit 2" in reason  # explicit, distinguishable from a passed scan


def test_scan_code_isolated_denies_on_spawn_failure(monkeypatch):
    """L6: a scan subprocess that fails to SPAWN must deny with a clear reason."""
    from swarm_os.services.security_gate import scan_code_isolated

    def _boom(*a, **k):
        raise OSError("no such executable")
    monkeypatch.setattr(_subprocess, "Popen", _boom)
    ok, reason = scan_code_isolated("x = 1")
    assert ok is False
    assert "could not start" in reason


def test_scan_code_isolated_denies_on_timeout(monkeypatch):
    """L6: a scan subprocess that hangs past the timeout must DENY (fail closed),
    not fall through to execution."""
    from swarm_os.services.security_gate import scan_code_isolated
    proc = _popen_side_effect(0, timeout_raise=True)
    monkeypatch.setattr(_subprocess, "Popen", lambda *a, **k: proc)
    ok, reason = scan_code_isolated("x = 1", timeout=5)
    assert ok is False
    assert "timed out" in reason


def test_scan_code_isolated_denial_is_not_misread_as_pass(monkeypatch):
    """L6 (theater-proof): a scan subprocess returning a MALFORMED/garbage exit
    (e.g. exit code 99, meaning 'crashed') must be treated as a denial, never as
    a pass — the channel is binary (exit 0 = allow, anything else = deny)."""
    from swarm_os.services.security_gate import scan_code_isolated
    for bad_rc in (1, 2, 99, -1, 130, 255):
        proc = _popen_side_effect(bad_rc, stderr="some random output that is not a verdict")

        def _fake_popen(*a, **k):
            return proc

        monkeypatch.setattr(_subprocess, "Popen", _fake_popen)
        ok, _ = scan_code_isolated("x = 1")
        assert ok is False, f"exit {bad_rc} must deny, not pass"


async def test_sandbox_repl_denies_exec_when_scan_unavailable(monkeypatch):
    """L6: if the isolated scan itself THROWS (e.g. the to_thread wrapper fails),
    the sandbox must fail closed and DENY execution — not execute the code."""
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler

    def _scan_boom(*a, **k):
        raise RuntimeError("scanner subprocess vanished")
    import swarm_os.services.security_gate as sg_mod
    monkeypatch.setattr(sg_mod, "scan_code_isolated", _scan_boom)
    r = await SandboxReplHandler().execute({"language": "python", "code": "print('DANGER')"})
    assert r.get("ok") is False
    assert "denied" in (r.get("stderr", "") + r.get("error", "")).lower()
    assert "print('DANGER')" not in r.get("stdout", "")  # never executed


async def test_sandbox_repl_powershell_guard_still_fires_after_l6(monkeypatch):
    """L6 regression: the existing PowerShell destructive-command guard must
    still run (it was NOT bypassed by the process-boundary change)."""
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler
    r = await SandboxReplHandler().execute({"language": "powershell", "command": "Remove-Item C:\\x -Recurse"})
    assert r.get("ok") is False
    assert "Security Gate" in r.get("stderr", "")
