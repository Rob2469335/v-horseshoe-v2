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

from tests.conftest import run_approved


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
        return {
            "content": "ok",
            "error": None,
            "tools_used": [],
            "model": "t",
            "elapsed": 1.0,
            "total_tokens": 5,
        }

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

        async def count(self, **kw):
            return 0

        async def search(self, **kw):
            return []

        async def upsert(self, **kw):
            return True

        async def delete(self, **kw):
            return True

        async def create_collection(self, **kw):
            return None

    class FakeGraphRepo:
        def get_node_count(self):
            return 0

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

    r = await SandboxReplHandler().execute(
        {"language": "python", "code": "import subprocess; subprocess.run(['whoami'])"}
    )
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

    for bad in (
        "Remove-Item C:\\x -Recurse",
        "Stop-Service spooler",
        "rm -rf /",
        "shutdown /s",
    ):
        r = await SandboxReplHandler().execute(
            {"language": "powershell", "command": bad}
        )
        assert r.get("ok") is False
        assert "Security Gate" in r.get("stderr", ""), bad


def test_legacy_runtime_classifies_playwright_writes_as_state_changing():
    """/tools/execute dispatches through agent_runtime.call_tool whose only gate
    is is_state_changing. Browser input ops (click/type/fill/press) must be
    classified state-changing so they require approval — previously playwright
    was never checked, so a loopback caller could drive the persistent
    logged-in browser with no approval. Regression for the 2026-08-17 audit."""
    from swarm_os.agent_runtime import AgentRuntime
    from swarm_os.exceptions import ApprovalRequiredError

    rt = AgentRuntime.__new__(AgentRuntime)
    rt.approved_actions = []

    for op in ("click", "browser_type", "fill_form", "browser_press_key", "select"):
        assert rt.is_state_changing(
            "playwright", {"operation": op, "name": "Transfer"}
        ) is True, op
    for op in ("navigate", "screenshot", "extract_text", "browser_state", "wait"):
        assert rt.is_state_changing(
            "playwright", {"operation": op, "url": "https://example.com"}
        ) is False, op

    async def _expect_approval():
        try:
            await rt.call_tool(
                "playwright",
                {"operation": "click", "name": "Transfer"},
            )
        except ApprovalRequiredError:
            return True
        return False

    import asyncio

    assert asyncio.run(_expect_approval()) is True


@pytest.mark.asyncio
async def test_sandbox_repl_kills_proc_on_cancel(monkeypatch):
    """asyncio.CancelledError inherits BaseException — the except TimeoutError
    branch never fires on a cancelled/abandoned stream, so a finally must kill
    the subprocess or an orphaned `python -I` runs up to the full timeout.
    Regression for the 2026-08-17 audit finding (same shape as the danger_room
    fix)."""
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler

    class _CancellingProc:
        def __init__(self):
            self.returncode = None
            self.killed = False

        async def communicate(self):
            raise asyncio.CancelledError("test cancellation")

        def kill(self):
            self.killed = True

        async def wait(self):
            return None

    created = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        p = _CancellingProc()
        created.append(p)
        return p

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    with pytest.raises(asyncio.CancelledError):
        await SandboxReplHandler().execute(
            {"language": "python", "code": "import time; time.sleep(30)"}
        )
    assert created, "subprocess must have been created"
    assert created[0].killed is True


@pytest.mark.asyncio
async def test_sandbox_repl_pytest_rejects_flag_and_outside_path(monkeypatch):
    """sandbox_repl pytest branch: a flag-like path (--junitxml=...) or a path
    outside the project root must be blocked before reaching pytest — an
    injected flag would write outside the sandbox, --pdb drops into an
    interactive debugger. Regression for the 2026-08-17 audit finding."""
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler

    h = SandboxReplHandler()
    for bad in (
        "--junitxml=C:/Windows/Temp/pwned.xml",
        "-x",
        "",
        r"C:\Windows\System32\pwned.py",
    ):
        r = await h.execute({"language": "pytest", "path": bad})
        assert r.get("ok") is False, bad
        assert "Security Gate" in r.get("stderr", ""), bad


@pytest.mark.asyncio
async def test_sandbox_repl_pytest_runs_real_target():
    """A real in-project pytest target still runs through the sandbox branch
    (flag rejection must not break the legitimate flow)."""
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler

    r = await SandboxReplHandler().execute(
        {"language": "pytest", "path": "tests/test_security_hardening.py::test_security_gate_scan_code"}
    )
    assert r.get("returncode") == 0, r.get("stdout", "")[-200:]
    assert "1 passed" in r.get("stdout", "")


# ── security: screen self-promotion blocked ─────────────────────────────────
    from swarm_os.lib.mcp import screen as s

    s.SCREEN_AUTONOMOUS = False
    r = s.screen_handler({"action": "set_screen_autonomous", "value": True})
    assert "HUMAN-CONTROL" in r.get("error", "")
    assert s.SCREEN_AUTONOMOUS is False


@pytest.mark.skipif(
    sys.platform != "win32", reason="screen-control is Windows-only (ctypes.windll)"
)
def test_screen_reset_blocked_in_human_mode(monkeypatch):
    from swarm_os.lib.mcp import screen as s

    s.SCREEN_AUTONOMOUS = False
    r = s.screen_handler({"action": "reset_screen_action_count"})
    assert "HUMAN-CONTROL" in r.get("error", "")


# ── security: mcp_register allowlist ────────────────────────────────────────
@pytest.mark.asyncio
async def test_mcp_register_rejects_shell_metachars(monkeypatch):
    from runtime_v2.services.tool_executor import run

    payload = {
        "server_name": "evil",
        "command": "python -c 'import os' && rm -rf /",
        "args": [],
    }
    result = await run_approved(run, "mcp_register", payload)
    assert result.get("ok") is False
    assert "Security Gate" in result.get("error", "")


@pytest.mark.asyncio
async def test_mcp_register_rejects_non_allowlisted_launcher(monkeypatch):
    from runtime_v2.services.tool_executor import run

    payload = {"server_name": "evil", "command": "powershell -c whoami", "args": []}
    result = await run_approved(run, "mcp_register", payload)
    assert result.get("ok") is False
    assert "Security Gate" in result.get("error", "")


@pytest.mark.asyncio
async def test_mcp_register_rejects_non_list_args(monkeypatch, tmp_path):
    # args as a single string would smuggle an un-split argument list past the
    # per-element guards (string iteration checks characters, not arguments).
    # Deliberately metachar-free so the old char-iteration check cannot catch it.
    import runtime_v2.services.tool_executor as te

    monkeypatch.setattr(te, "_ROOT", tmp_path)
    payload = {"server_name": "evil", "command": "python", "args": "-c print('pwn')"}
    result = await run_approved(te.run, "mcp_register", payload)
    assert result.get("ok") is False
    assert "Security Gate" in result.get("error", "")
    assert not (tmp_path / "swarm_config.json").exists()


@pytest.mark.asyncio
async def test_mcp_register_rejects_non_string_args(monkeypatch, tmp_path):
    # args as a dict or int must also fail closed, not pass the guards.
    import runtime_v2.services.tool_executor as te

    monkeypatch.setattr(te, "_ROOT", tmp_path)
    for bad_args in ({"key": "value"}, 42):
        payload = {"server_name": "evil", "command": "python", "args": bad_args}
        result = await run_approved(te.run, "mcp_register", payload)
        assert result.get("ok") is False, f"args={bad_args!r} was not rejected"
        assert "Security Gate" in result.get("error", "")
    assert not (tmp_path / "swarm_config.json").exists()


# ── security: web_fetch SSRF ────────────────────────────────────────────────
def test_ssrf_blocks_internal_and_metadata():
    from swarm_os.lib.mcp.web_search import _ssrf_check

    for url in (
        "http://127.0.0.1:6333/x",
        "http://localhost:8080/v1/models",
        "http://169.254.169.254/latest/",
        "http://192.168.1.10/x",
    ):
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

    fm.record_model_failure(
        "openrouter/bill:free", "402 Insufficient Balance", permanent=True
    )
    try:
        key = fm._cooldown_key("openrouter/bill:free")
        entry = fm._cooldowns.get(key)
        assert entry is not None
        assert entry["until"] == float("inf")
        # Still cooled down (never auto-recovers).
        assert fm.is_model_cooled_down("openrouter/bill:free") is True
    finally:
        fm.clear_model_cooldown("openrouter/bill:free")


def test_clear_model_cooldown_is_scoped_to_one_model():
    """The manual clear must target ONE model only — it must NOT wipe the
    legitimate exponential-backoff cooldowns of OTHER transiently
    rate-limited models."""
    from runtime_v2.services import fallback_manager as fm

    fm.record_model_failure("openrouter/a:free", "boom")  # transient backoff
    fm.record_model_failure(
        "openrouter/bill:free", "402 Insufficient Balance", permanent=True
    )
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
        fm.clear_model_cooldown("openrouter/bill:free")


def test_success_does_not_clear_permanent_pin():
    """A permanent billing pin (until=inf) must SURVIVE a concurrent success —
    record_model_success clears transient cooldowns only. An in-flight request
    that succeeds after a 402 pin was written must not silently un-pin the
    doomed provider (the fail-closed billing contract), or the chain starts
    retrying a broken key and the billing problem disappears. Only the manual,
    model-scoped clear_model_cooldown lifts a permanent pin."""
    from runtime_v2.services import fallback_manager as fm

    fm.record_model_failure(
        "openrouter/billpin:free", "402 Insufficient Balance", permanent=True
    )
    try:
        assert fm.is_model_cooled_down("openrouter/billpin:free") is True
        # A success arriving after the pin must NOT clear it.
        fm.record_model_success("openrouter/billpin:free")
        assert fm.is_model_cooled_down("openrouter/billpin:free") is True, (
            "permanent billing pin was cleared by a success — the fail-closed "
            "billing contract is broken"
        )
        entry = fm._cooldowns.get(fm._cooldown_key("openrouter/billpin:free"))
        assert entry is not None and entry["until"] == float("inf")
        # The manual clear is still the only exit.
        assert fm.clear_model_cooldown("openrouter/billpin:free") is True
        assert not fm.is_model_cooled_down("openrouter/billpin:free")
    finally:
        fm.clear_model_cooldown("openrouter/billpin:free")


def test_success_clears_transient_cooldown():
    """A NON-permanent (finite-window) cooldown IS cleared by success — that
    behavior is unchanged; only the permanent pin is protected."""
    from runtime_v2.services import fallback_manager as fm

    fm.record_model_failure("openrouter/transient:free", "boom")
    try:
        assert fm.is_model_cooled_down("openrouter/transient:free") is True
        fm.record_model_success("openrouter/transient:free")
        assert not fm.is_model_cooled_down("openrouter/transient:free")
    finally:
        fm.record_model_success("openrouter/transient:free")


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


# ── security gate: sandbox-escape shape regression tests (2026-08-17) ────────
# The 2026-08-17 audit found the AST gate bypassable end-to-end: `import
# os.path; os.system(...)`, `o = os; o.system(...)`, `from os import *`,
# `os.__dict__['system'](...)`, `vars(os)['system'](...)`,
# `getattr(__builtins__, 'exec')`, and pathlib/network modules in LLM snippets.
# Each shape below was empirically verified as a REAL execution vector before
# the fix (the code ran); each must now DENY. Revert-proof.
def test_security_gate_blocks_dotted_os_import_attr_call():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation

    with pytest.raises(SecurityGateViolation):
        SecurityGate.scan_code("import os.path\nos.system('echo pwned')")


def test_security_gate_blocks_os_rebinding():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation

    for bad in (
        "import os\no = os\no.system('whoami')",
        "import os\nos2 = os\nos2.system('whoami')",
        "import os\na, b = (1, os)\nb.system('whoami')",
    ):
        with pytest.raises(SecurityGateViolation):
            SecurityGate.scan_code(bad)


def test_security_gate_allows_os_path_rebinding_read():
    # `o = os.path; o.abspath('.')` stays ALLOWED — os.path has no .system and
    # abspath is a read, not a banned os attribute.
    from swarm_os.services.security_gate import SecurityGate

    SecurityGate.scan_code("import os\no = os.path\nprint(o.abspath('.'))")


def test_security_gate_blocks_os_star_import():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation

    with pytest.raises(SecurityGateViolation):
        SecurityGate.scan_code("from os import *\nsystem('whoami')")


def test_security_gate_blocks_os_namespace_subscript():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation

    for bad in (
        "import os\nos.__dict__['system']('whoami')",
        "import os\nvars(os)['system']('whoami')",
    ):
        with pytest.raises(SecurityGateViolation):
            SecurityGate.scan_code(bad)


def test_security_gate_blocks_builtins_getattr_reflection():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation

    with pytest.raises(SecurityGateViolation):
        SecurityGate.scan_code("getattr(__builtins__, 'exec')('print(1)')")


def test_security_gate_strict_blocks_pathlib_and_network():
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation

    # scan_code is strict by default: LLM snippets may not use pathlib file I/O
    # or network clients (repo files may — scan_file stays non-strict).
    for bad in (
        "from pathlib import Path\nPath('x.txt').write_text('pwned')",
        "import pathlib\npathlib.Path('x').read_text()",
        "import urllib.request\nurllib.request.urlopen('http://127.0.0.1:8000')",
        "import requests\nrequests.get('http://127.0.0.1:8000')",
        "import httpx\nhttpx.post('http://127.0.0.1:8000', json={})",
    ):
        with pytest.raises(SecurityGateViolation):
            SecurityGate.scan_code(bad)


def test_security_gate_non_strict_allows_pathlib_and_network_for_repo():
    # scan_file path (strict=False) must still accept the repo's own legit use
    # of pathlib/requests/httpx — otherwise danger_room's sandbox copy scan
    # would reject the project's own source.
    from swarm_os.services.security_gate import SecurityGate

    SecurityGate.scan_code(
        "from pathlib import Path\nPath('x').exists()",
        strict=False,
    )
    SecurityGate.scan_code(
        "import requests\nr = requests.get('http://127.0.0.1:8000/readyz')",
        strict=False,
    )
    SecurityGate.scan_code(
        "import httpx\nclient = httpx.Client()\nprint(client)",
        strict=False,
    )


@pytest.mark.asyncio
async def test_sandbox_repl_allows_readonly_os_code():
    # `import os; os.walk('.')` (the debugger's natural file-listing snippet)
    # must pass the gate so the debugger path doesn't loop-trip on it. Runs the
    # real isolated scan subprocess (module fixture keeps Popen real for this).
    from swarm_os.services.security_gate import SecurityGate

    SecurityGate.scan_code(
        "import os\nfor _, dirs, files in os.walk('.'):\n    print(dirs)"
    )
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler

    r = await SandboxReplHandler().execute(
        {"language": "python", "code": "import os; print(len(list(os.walk('.'))))"}
    )
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
        proc = _popen_side_effect(
            bad_rc, stderr="some random output that is not a verdict"
        )

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
    r = await SandboxReplHandler().execute(
        {"language": "python", "code": "print('DANGER')"}
    )
    assert r.get("ok") is False
    assert "denied" in (r.get("stderr", "") + r.get("error", "")).lower()
    assert "print('DANGER')" not in r.get("stdout", "")  # never executed


async def test_sandbox_repl_powershell_guard_still_fires_after_l6(monkeypatch):
    """L6 regression: the existing PowerShell destructive-command guard must
    still run (it was NOT bypassed by the process-boundary change)."""
    from swarm_os.capabilities.sandbox_repl import SandboxReplHandler

    r = await SandboxReplHandler().execute(
        {"language": "powershell", "command": "Remove-Item C:\\x -Recurse"}
    )
    assert r.get("ok") is False
    assert "Security Gate" in r.get("stderr", "")


def test_security_gate_blocks_builtins_exec():
    """The builtins namespace must not smuggle banned calls past the gate:
    `import builtins; builtins.exec(...)` and `getattr(builtins, 'exec')` and
    `__builtins__.exec(...)` all resolve the exec builtin under an Attribute
    (never a Name call), escaping the original visit_Call scan."""
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation

    cases = [
        "import builtins; builtins.exec('import subprocess')",
        "import builtins; getattr(builtins, 'exec')('import subprocess')",
        "import builtins as b; b.eval('x')",
        "__builtins__.exec('print(1)')",
        "__builtins__['__import__']('os')",
    ]
    for code in cases:
        try:
            SecurityGate.scan_code(code)
        except SecurityGateViolation:
            continue
        raise AssertionError(f"builtins bypass not blocked: {code}")


def test_security_gate_object_method_named_exec_not_blocked():
    """A harmless attribute READ/call on a NON-builtins object (e.g. a duck-typed
    method named 'exec') must NOT trigger the gate — the fix targets the
    builtins namespace only, not every `.exec` in the codebase."""
    from swarm_os.services.security_gate import SecurityGate

    SecurityGate.scan_code("x = obj.exec")
    SecurityGate.scan_code("result = my_object.exec(argument)")


@pytest.mark.asyncio
async def test_dangerroom_rejects_pytest_flags(monkeypatch):
    """A flag-like test target (--junitxml=..., -x, -k) must be rejected before
    it reaches pytest — passing it through would let an injected flag write
    outside the sandbox or alter the test run. Only sandbox-contained paths
    pass."""
    from swarm_os.services.danger_room import DangerRoom

    tmp_path = None
    captured_cmd = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    import tempfile
    from pathlib import Path

    tmp_path = Path(tempfile.mkdtemp())
    try:
        dr = DangerRoom(tmp_path)
        dr.is_active = True
        dr.sandbox_dir = tmp_path
        res = await dr.run_tests(
            test_targets=[
                "--junitxml=/tmp/pwned.xml",  # flag -> rejected
                "-x",  # flag -> rejected
                str(tmp_path / "tests" / "test_a.py"),  # inside sandbox -> kept
            ]
        )
        assert res["ok"] is True
        cmd = captured_cmd["cmd"]
        assert "--junitxml=/tmp/pwned.xml" not in cmd
        assert "-x" not in cmd
        assert str(tmp_path / "tests" / "test_a.py") in cmd
        assert "--" in cmd  # separator: remaining args are files, not options
    finally:
        import shutil

        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_dangerroom_kills_proc_on_cancel(monkeypatch):
    """asyncio.CancelledError inherits BaseException, so the except TimeoutError
    branch never fires on a cancelled task — the subprocess must still be killed
    by a finally block, else an abandoned test run leaks a pytest process inside
    the sandbox."""
    from swarm_os.services.danger_room import DangerRoom
    import tempfile
    from pathlib import Path

    tmp_path = Path(tempfile.mkdtemp())
    try:
        dr = DangerRoom(tmp_path)
        dr.is_active = True
        dr.sandbox_dir = tmp_path

        class _CancellingProc:
            def __init__(self):
                self.returncode = None
                self.killed = False

            async def communicate(self):
                raise asyncio.CancelledError("test cancellation")

            def kill(self):
                self.killed = True

            async def wait(self):
                return None

        created = []

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            p = _CancellingProc()
            created.append(p)
            return p

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", fake_create_subprocess_exec
        )
        target = tmp_path / "tests" / "test_a.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        with pytest.raises(asyncio.CancelledError):
            await dr.run_tests(test_targets=[str(target)])
        assert created, "subprocess must have been created"
        assert created[0].killed is True
    finally:
        import shutil

        shutil.rmtree(tmp_path, ignore_errors=True)


def test_dangerroom_excludes_env_from_sandbox(tmp_path):
    """LIVE SECRETS: .env (API keys, OAuth creds) must never be copied into the
    DangerRoom sandbox — LLM-generated mutation/recovery code there could read it
    and exfiltrate. clean_sandbox_env strips the process env; this excludes the
    file on disk. Empirically verified pre-fix: sandbox .env existed (1763 bytes)."""
    import asyncio
    from swarm_os.services.danger_room import DangerRoom

    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".env").write_text("SECRET_API_KEY=live-key-123\n", encoding="utf-8")
    (root / ".env.example").write_text("SECRET_API_KEY=\n", encoding="utf-8")
    (root / "keep.py").write_text("x = 1\n", encoding="utf-8")

    dr = DangerRoom(root)
    asyncio.run(dr.setup())
    try:
        assert not (dr.sandbox_dir / ".env").exists()
        assert not (dr.sandbox_dir / ".env.example").exists()
        assert (dr.sandbox_dir / "keep.py").exists()
    finally:
        asyncio.run(dr.teardown())


def test_swarm_config_mcp_servers_are_valid():
    """The shipped swarm_config.json MCP registrations must always parse with
    string commands/args and no shell metacharacters. swarm_config.json is
    human-authored (trust boundary: the developer), so it may use direct
    executable paths as well as the npx/node/python/uvx launchers that
    mcp_register (LLM-supplied) is restricted to — a malformed entry would
    break external-MCP tool loading at startup."""
    import json
    from pathlib import Path

    cfg = json.loads(Path("swarm_config.json").read_text(encoding="utf-8"))
    servers = cfg["mcp_servers"]
    assert servers, "swarm_config.json must register at least one MCP server"
    allowed = ("npx", "node", "python", "python3", "uvx", "semgrep")
    meta = ("&&", "||", ";", "|", "$(", "`", "&", "\n", "\r", ">", "<")
    for name, s in servers.items():
        assert s.get("command"), f"server {name} missing command"
        cmd = s["command"].strip().lower()
        assert cmd in allowed or cmd.endswith(".exe"), (
            f"{name}: launcher {s['command']} not allowlisted and not an executable path"
        )
        args = s.get("args", [])
        assert isinstance(args, list) and all(isinstance(a, str) for a in args), (
            f"{name}: args must be list[str]"
        )
        assert not any(ch in "".join(args) for ch in meta), f"{name}: metachar in args"
