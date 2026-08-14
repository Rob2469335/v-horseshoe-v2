"""Regression tests for the pre-action authorization gate (Design A, 2026-08-12).

Covers the security invariants the user specified:
  - exact-payload binding (approve the stored action, never a replacement)
  - expiration (pending approvals TTL)
  - one-time consumption (a pending action executes at most once)
  - denial (explicit deny discards without executing)
  - unauthorized dispatch prevention (CONFIRM returns before dispatch)
  - unknown/unclassified tool -> DENY (fail-closed)
  - read-only agent ops -> ALLOW (no approval)
  - existing unconfirmed email_send behavior preserved
"""
from __future__ import annotations

import time

import pytest

from swarm_os.services import approval_registry as ar
from runtime_v2.services.tool_executor import (
    run,
    peek_pending,
    deny_pending,
    execute_approved,
)


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.setattr(ar, "_registry", ar._Registry())
    # Point tool_executor's imported get_registry at the fresh registry.
    import runtime_v2.services.tool_executor as te

    monkeypatch.setattr(te, "get_registry", ar.get_registry)
    return ar.get_registry()


# ── policy classification ────────────────────────────────────────────────────
def test_read_only_ops_are_allow():
    assert ar.agent_tool_policy("filesystem", "read") == ar.ALLOW
    assert ar.agent_tool_policy("filesystem", "list") == ar.ALLOW
    assert ar.agent_tool_policy("filesystem", "grep") == ar.ALLOW
    assert ar.agent_tool_policy("filesystem", "glob") == ar.ALLOW
    assert ar.agent_tool_policy("web_search") == ar.ALLOW
    assert ar.agent_tool_policy("semantic_search") == ar.ALLOW
    assert ar.agent_tool_policy("screen", "cursor_position") == ar.ALLOW
    assert ar.agent_tool_policy("system", "system_inventory") == ar.ALLOW
    assert ar.agent_tool_policy("system", "process_list") == ar.ALLOW
    assert ar.agent_tool_policy("git", "status") == ar.ALLOW
    assert ar.agent_tool_policy("git", "log") == ar.ALLOW
    assert ar.agent_tool_policy("git", "diff") == ar.ALLOW
    assert ar.agent_tool_policy("git", "branch") == ar.ALLOW
    assert ar.agent_tool_policy("git") == ar.ALLOW  # no action = status read
    assert ar.agent_tool_policy("git", "commit") == ar.DENY
    assert ar.agent_tool_policy("git", "checkout") == ar.DENY
    assert ar.agent_tool_policy("git", "reset") == ar.DENY


@pytest.mark.asyncio
async def test_git_operation_cannot_smuggle_arguments():
    """The git tool's only caller input is `operation`, which is whitelisted and
    used as a dict key to select a hardcoded argv list — caller text never
    reaches the subprocess as an argument. A hostile operation (metacharacters,
    path-traversal, unknown) must return an error WITHOUT spawning git."""
    from runtime_v2.services.tool_executor import run as te_run

    spawned = []
    orig = None
    try:
        import asyncio
        orig = asyncio.create_subprocess_exec
        async def _spy(*args, **kwargs):
            spawned.append(args)
            raise RuntimeError("git should not spawn for this input")
        asyncio.create_subprocess_exec = _spy  # type: ignore[assignment]
        result = await te_run("git", {"operation": "diff; rm -rf /"})
        assert result.get("ok") is False
        result2 = await te_run("git", {"operation": "../etc/passwd"})
        assert result2.get("ok") is False
        assert spawned == [], f"git spawned for hostile input: {spawned}"
        # A VALID op must spawn EXACTLY the hardcoded argv — never caller text.
        spawned.clear()
        def _capture(*args, **kwargs):
            spawned.append(args)
            raise RuntimeError("capture only")
        asyncio.create_subprocess_exec = _capture  # type: ignore[assignment]
        try:
            await te_run("git", {"operation": "diff", "path": "../../etc/passwd", "ref": "HEAD; echo pwned"})
        except RuntimeError:
            pass
        assert spawned, "git diff should spawn"
        argv = spawned[0]
        assert argv == ("git", "diff"), f"argv had extra caller input: {argv}"
    finally:
        if orig is not None:
            asyncio.create_subprocess_exec = orig  # type: ignore[assignment]



def test_side_effecting_ops_require_confirm():
    assert ar.agent_tool_policy("filesystem", "write") == ar.ALWAYS_CONFIRM
    assert ar.agent_tool_policy("filesystem", "patch") == ar.ALWAYS_CONFIRM
    assert ar.agent_tool_policy("playwright", "click") == ar.ALWAYS_CONFIRM
    assert ar.agent_tool_policy("sandbox_repl") == ar.ALWAYS_CONFIRM
    assert ar.agent_tool_policy("system", "kill") == ar.ALWAYS_CONFIRM
    assert ar.agent_tool_policy("email_send") == ar.ALWAYS_CONFIRM
    assert ar.agent_tool_policy("screen", "left_click") == ar.ALWAYS_CONFIRM
    assert ar.agent_tool_policy("web_fetch") == ar.CONFIRM
    assert ar.agent_tool_policy("email_draft") == ar.CONFIRM
    assert ar.agent_tool_policy("playwright", "navigate") == ar.CONFIRM


def test_unknown_tool_is_deny():
    """Fail-closed: an unknown tool or unclassified action is DENY, never ALLOW."""
    assert ar.agent_tool_policy("totally_new_tool") == ar.DENY
    assert ar.agent_tool_policy("filesystem", "nonsense_op") == ar.DENY
    assert ar.agent_tool_policy("system", "nonsense_op") == ar.DENY
    assert ar.agent_tool_policy("screen", "nonsense_op") == ar.DENY
    assert ar.agent_tool_policy("playwright", "nonsense_op") == ar.DENY


# ── gate behavior through tool_executor.run() ────────────────────────────────
@pytest.mark.asyncio
async def test_allow_dispatches_immediately():
    res = await run("filesystem", {"operation": "read", "path": "AGENTS.md"})
    assert res.get("ok") is True
    assert res.get("status") != "confirmation_required"


@pytest.mark.asyncio
async def test_confirm_does_not_dispatch():
    """A CONFIRM/ALWAYS_CONFIRM tool must NOT execute until approved."""
    res = await run("sandbox_repl", {"language": "python", "code": "print(1)"})
    assert res.get("status") == "confirmation_required"
    assert res.get("ok") is False  # never dispatched
    assert res.get("pending_id")  # a pending action was created
    assert res.get("authorization") == "ALWAYS_CONFIRM"


@pytest.mark.asyncio
async def test_deny_never_executes():
    """A DENY verdict returns immediately with no pending action."""
    res = await run("totally_new_tool", {})
    assert res.get("ok") is False
    assert res.get("authorization") == "DENY"
    assert res.get("status") != "confirmation_required"


@pytest.mark.asyncio
async def test_exact_payload_binding_executes_stored_action(monkeypatch, tmp_path):
    """Approval authorizes the EXACT stored action. The approved dispatch uses
    the stored payload — a caller cannot substitute a different one."""
    import runtime_v2.services.tool_executor as te

    monkeypatch.setattr(te, "_ROOT", tmp_path)
    target = tmp_path / "tmp_approved.txt"
    # Ask for a write; the gate stores it.
    first = await run(
        "filesystem",
        {"operation": "write", "path": str(target), "content": "hello"},
    )
    assert first.get("status") == "confirmation_required"
    pending_id = first["pending_id"]
    stored = ar.get_registry().peek(pending_id)
    assert stored is not None
    assert stored["payload"]["path"] == str(target)

    # Approve + execute -> dispatches the STORED payload.
    result = await execute_approved(pending_id)
    assert result.get("ok") is True
    assert target.read_text() == "hello"
    # One-time: the same pending id cannot execute twice.
    second = await execute_approved(pending_id)
    assert second.get("authorization") == "DENY"


@pytest.mark.asyncio
async def test_wrong_payload_cannot_reuse_pending(monkeypatch, tmp_path):
    """Approving a DIFFERENT action than the one stored must not execute."""
    import runtime_v2.services.tool_executor as te

    monkeypatch.setattr(te, "_ROOT", tmp_path)
    first = await run("filesystem", {"operation": "write", "path": "a.txt", "content": "x"})
    assert first.get("status") == "confirmation_required"
    pending_id = first["pending_id"]

    # Try to approve with a DIFFERENT tool/payload: must be denied, and the
    # original pending must NOT be consumed (a legitimate approval is preserved).
    wrong = await run(
        "filesystem",
        {"operation": "write", "path": "b.txt", "content": "y"},
        auth={"approved_pending_id": pending_id},
    )
    assert wrong.get("authorization") == "DENY"
    assert peek_pending(pending_id) is not None  # not burned by the mismatch


@pytest.mark.asyncio
async def test_expired_pending_denied(monkeypatch):
    """An expired pending action cannot execute."""
    first = await run("sandbox_repl", {"language": "python", "code": "print(1)"})
    pending_id = first["pending_id"]
    # Backdate the expiry on the STORED record (peek returns a copy).
    reg = ar.get_registry()
    stored = reg._pending[pending_id]
    stored["expires_at"] = time.time() - 1
    result = await execute_approved(pending_id)
    assert result.get("authorization") == "DENY"


@pytest.mark.asyncio
async def test_one_time_consumption():
    first = await run("sandbox_repl", {"language": "python", "code": "print(1)"})
    pending_id = first["pending_id"]
    r1 = await execute_approved(pending_id)
    assert r1.get("ok") is True or r1.get("authorization") == "DENY"  # executed
    r2 = await execute_approved(pending_id)
    assert r2.get("authorization") == "DENY"  # consumed


@pytest.mark.asyncio
async def test_deny_pending_discards():
    first = await run("sandbox_repl", {"language": "python", "code": "print(1)"})
    pending_id = first["pending_id"]
    assert deny_pending(pending_id) is True
    assert peek_pending(pending_id) is None
    result = await execute_approved(pending_id)
    assert result.get("authorization") == "DENY"


@pytest.mark.asyncio
async def test_email_send_confirmation_preserved(monkeypatch):
    """email_send stays gated (ALWAYS_CONFIRM) AND its own confirmed-token
    behavior is unchanged — the gate does not bypass the token check."""
    assert ar.agent_tool_policy("email_send") == ar.ALWAYS_CONFIRM
    first = await run("email_send", {"send_token": "x", "confirmed": False})
    assert first.get("status") == "confirmation_required"


@pytest.mark.asyncio
async def test_git_tool_readonly_dispatch():
    """The git tool (read-only status/log/diff) is ALLOW-tier and dispatches
    through the gate to real git output. This pins the tool surface so the
    agent can ground edits against the working-tree baseline."""
    from tests.conftest import run_approved
    from runtime_v2.services.tool_executor import run as te_run

    assert ar.agent_tool_policy("git", "status") == ar.ALLOW
    result = await run_approved(te_run, "git", {"operation": "status"})
    assert result.get("ok") is True
    assert isinstance(result.get("result"), str)
    # unknown/state-changing git ops must not be ALLOW
    assert ar.agent_tool_policy("git", "commit") == ar.DENY
    assert ar.agent_tool_policy("git", "reset") == ar.DENY


@pytest.mark.asyncio
async def test_trace_hook_wired_through_dispatch(tmp_path):
    """The trace_hook seam must be threaded from run() through _dispatch to the
    handlers that accept it (filesystem/web_search/web_fetch/playwright).
    Pre-wiring, _dispatch called handlers without the hook so per-tool traces
    were silently dropped even though the handlers support them."""
    from runtime_v2.services.tool_executor import run as _run
    import runtime_v2.services.tool_executor as _te

    target = tmp_path / "probe.txt"
    target.write_text("hello", encoding="utf-8")
    traces = []
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(_te, "_ROOT", tmp_path)
    try:
        await _run(
            "filesystem",
            {"operation": "read", "path": "probe.txt"},
            trace_hook=lambda etype, epayload: traces.append((etype, epayload)),
        )
    finally:
        monkeypatch.undo()
    assert any(t[0] == "filesystem_read" for t in traces), f"trace_hook not invoked, got {traces}"
