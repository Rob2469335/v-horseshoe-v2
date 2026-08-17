"""Tests for the Command Center SOTA permission model (Builds 1 + 2, 2026).

Build 1 — risk-classified permission tiers (two axes: tier + channel):
  tier:    free / ask / important / approval (important always confirms even in auto)
  channel: agent / human (fail-closed: UNKNOWN action -> human, never agent)
  scheduler ceiling hook: important/approval/human-channel -> hard-block.

Build 2 — per-app OS tiers folded into Build 1's domain-scoped grants: a
  browser's screen-input tier keys on the ACTIVE TAB's domain, not the exe.

Builds 3 + 4 are NOT built (scheduler + takeover deferred pending review / a
real threat model) — their code does not exist in the tree.
"""

import asyncio
import time

import pytest

from swarm_os.services import permission_tiers as pt


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "GRANTS_FILE", tmp_path / "grants.json")
    monkeypatch.setattr(pt, "_grants_cache", None)
    return tmp_path


# ── Build 1: risk-classified tiers ─────────────────────────────────────────
def test_base_tier_classification():
    assert pt.base_tier("read") == "free"
    assert pt.base_tier("email_send") == "important"
    assert pt.base_tier("screen") == "approval"
    assert pt.base_tier("sandbox_repl") == "approval"
    assert pt.base_tier("filesystem", "write") == "ask"
    assert pt.base_tier("unknown_tool") == "ask"  # fail-closed


def test_important_always_confirms_even_in_auto_mode():
    # email_send must confirm even when auto_mode=True (never auto-approved).
    assert pt.needs_confirmation("gmail.com", "email_send", auto_mode=True) is True
    # a read is free even in auto mode.
    assert pt.needs_confirmation("gmail.com", "read", auto_mode=True) is False
    # 'write' is a HUMAN-channel action (it modifies) -> confirms even in auto.
    assert pt.needs_confirmation("example.com", "write", auto_mode=True) is True
    # an agent-safe ask-tier action (navigate) in auto mode -> no confirm.
    assert pt.needs_confirmation("example.com", "navigate", auto_mode=True) is False


def test_channel_fail_closed_unknown_is_human():
    """The critical fail-closed default: an UNKNOWN tool/action resolves to
    channel HUMAN, never agent — a future tool that touches a login/payment flow
    cannot silently let the agent perform it."""
    assert pt.channel_for("x", "totally_new_tool") == "human"
    assert pt.channel_for("x", "known_tool", "some_unseen_action") == "human"
    # Known agent-safe actions are agent.
    assert pt.channel_for("x", "read") == "agent"
    assert pt.channel_for("x", "browser_fill_form") == "agent"
    # Login/payment are human even with an agent tool name.
    assert (
        pt.channel_for("bank.com", "browser_type", "type into login field") == "human"
    )
    assert (
        pt.channel_for("shop.com", "browser_fill_form", "fill payment card") == "human"
    )
    assert pt.channel_for("gmail.com", "email_send") == "human"


def test_per_target_grant_overrides():
    pt.set_grant("gmail.com", "send", "important")
    assert pt.tier_for("gmail.com", "email_send") == "important"
    pt.set_grant("example.com", "read", "free")
    assert pt.tier_for("example.com", "read") == "free"
    assert pt.tier_for("example.com", "write") == "ask"


def test_grants_persist_across_load(monkeypatch, tmp_path):
    pt.set_grant("gmail.com", "send", "important")
    monkeypatch.setattr(pt, "_grants_cache", None)  # force reload
    assert pt.tier_for("gmail.com", "email_send") == "important"


def test_scheduler_ceiling_hook():
    """is_scheduler_allowed hard-blocks important/approval/human-channel."""
    assert pt.is_scheduler_allowed("x", "read") is True
    assert pt.is_scheduler_allowed("x", "glob") is True
    assert pt.is_scheduler_allowed("gmail.com", "email_send") is False
    assert pt.is_scheduler_allowed("x", "screen") is False
    assert pt.is_scheduler_allowed("x", "unknown_tool") is False


# ── Build 2: per-app OS tiers fold into domain-scoped grants ───────────────
def test_app_tier_resolves_grant(monkeypatch):
    import sys
    import types
    import swarm_os.lib.mcp.screen as sc

    # _app_tier does a LOCAL `import win32gui`, so inject a fake into sys.modules
    # so the local import resolves to it (then _app_name_from_hwnd is stubbed).
    fake_wg = types.ModuleType("win32gui")
    fake_wg.GetForegroundWindow = lambda: 123
    monkeypatch.setitem(sys.modules, "win32gui", fake_wg)
    monkeypatch.setattr(sc, "_app_name_from_hwnd", lambda hwnd: "calc.exe")
    # Grant calc.exe full-control -> approval tier maps to full-control.
    pt.set_grant("calc.exe", "screen", "approval")
    assert sc._app_tier() == "full-control"
    # Unlisted app (no grant) -> fail-closed view-only.
    monkeypatch.setattr(sc, "_app_name_from_hwnd", lambda hwnd: "unknown_app.exe")
    pt.set_grant("unknown_app.exe", "screen", "free")
    assert sc._app_tier() == "view-only"


def test_browser_tier_keys_on_domain_not_exe(monkeypatch):
    """A browser's screen tier resolves by ACTIVE TAB DOMAIN, not chrome.exe —
    a full-control grant on trusted-site.com leaves bank.com view-only."""
    import sys
    import types
    import swarm_os.lib.mcp.screen as sc
    import swarm_os.lib.mcp.playwright as pw

    fake_wg = types.ModuleType("win32gui")
    fake_wg.GetForegroundWindow = lambda: 123
    monkeypatch.setitem(sys.modules, "win32gui", fake_wg)
    monkeypatch.setattr(sc, "_app_name_from_hwnd", lambda hwnd: "chrome.exe")
    # Full control on trusted-site.com; chrome.exe itself has NO grant.
    pt.set_grant("trusted-site.com", "screen", "approval")
    monkeypatch.setattr(pw, "active_domain", lambda: "trusted-site.com")
    assert sc._app_tier() == "full-control"
    # Bank tab: chrome.exe still no grant, domain bank.com no grant -> view-only.
    monkeypatch.setattr(pw, "active_domain", lambda: "bank.com")
    assert sc._app_tier() == "view-only"


# ── Build 3: recurring task scheduler ───────────────────────────────────────
import datetime as _dt

from swarm_os.services import task_scheduler as ts


def test_task_crud(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "_TASKS_FILE", tmp_path / "tasks.json")
    t = ts.create_task("summarize my email inbox", "daily 08:00", enabled=True)
    assert t["id"]
    assert ts.list_tasks()[0]["goal"] == "summarize my email inbox"
    assert ts.set_task_enabled(t["id"], False) is True
    assert ts.list_tasks()[0]["enabled"] is False
    assert ts.delete_task(t["id"]) is True
    assert ts.list_tasks() == []


def test_is_due_daily(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "_TASKS_FILE", tmp_path / "tasks.json")
    t = ts.create_task("summarize my inbox", "daily 08:00")
    with ts._LOCK:
        data = ts._load()
        data[t["id"]]["last_run"] = (
            _dt.datetime.now() - _dt.timedelta(days=1)
        ).isoformat()
        ts._save(data)

    # Monkeypatch datetime.now to a fixed 09:00 today so the daily window passes.
    class _FakeDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            real = _dt.datetime.now()
            return cls(real.year, real.month, real.day, 9, 0)

    monkeypatch.setattr(ts, "datetime", _FakeDT)
    with ts._LOCK:
        data = ts._load()
    assert ts._is_due(data[t["id"]], time.time()) is True


def test_ceiling_gate_is_authority_before_keyword_scan(monkeypatch, tmp_path):
    """The permission-model check is LOAD-BEARING. A goal with a send hint is
    refused by is_scheduler_allowed (email_send is important) — the permission
    model stops it, not a string match. The keyword scan is defense-in-depth."""
    monkeypatch.setattr(ts, "_TASKS_FILE", tmp_path / "tasks.json")
    allowed, reason = ts._ceiling_gate("send an email to my boss")
    assert allowed is False
    assert "email_send" in reason  # refused by the permission model
    # A keyword-blocked goal (transaction) is also refused.
    allowed2, _ = ts._ceiling_gate("checkout my shopping cart")
    assert allowed2 is False


def test_unmapped_goal_refuses_not_dispatched(monkeypatch, tmp_path):
    """A goal that isn't a known-safe pattern REFUSES-AND-FLAGS — it is never
    dispatched to run_browser_task as a gamble."""
    monkeypatch.setattr(ts, "_TASKS_FILE", tmp_path / "tasks.json")
    t = ts.create_task("optimize my local database indexes", "hourly", enabled=True)
    with ts._LOCK:
        data = ts._load()
        data[t["id"]]["last_run"] = (
            _dt.datetime.now() - _dt.timedelta(hours=2)
        ).isoformat()
        ts._save(data)
    calls = {"n": 0}

    async def runner(task):
        calls["n"] += 1
        return {"ok": True}

    ran = asyncio.run(ts.run_due_tasks(runner=runner))
    assert ran == []
    assert calls["n"] == 0  # runner never called
    with ts._LOCK:
        result = ts._load()[t["id"]]["result"]
        assert result.get("blocked") == "unmapped_goal"


def test_safe_goal_dispatches(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "_TASKS_FILE", tmp_path / "tasks.json")
    t = ts.create_task("summarize my email inbox", "hourly", enabled=True)
    with ts._LOCK:
        data = ts._load()
        data[t["id"]]["last_run"] = (
            _dt.datetime.now() - _dt.timedelta(hours=2)
        ).isoformat()
        ts._save(data)
    calls = {"n": 0}

    async def runner(task):
        calls["n"] += 1
        return {"ok": True, "type": "email_summary"}

    ran = asyncio.run(ts.run_due_tasks(runner=runner))
    assert calls["n"] == 1
    assert ran == [t["id"]]


def test_important_goal_blocked_at_scheduler(monkeypatch, tmp_path):
    """'send an email' is important-tier -> hard-blocked, runner never called."""
    monkeypatch.setattr(ts, "_TASKS_FILE", tmp_path / "tasks.json")
    t = ts.create_task("send an email to my boss", "hourly", enabled=True)
    with ts._LOCK:
        data = ts._load()
        data[t["id"]]["last_run"] = (
            _dt.datetime.now() - _dt.timedelta(hours=2)
        ).isoformat()
        ts._save(data)
    calls = {"n": 0}

    async def runner(task):
        calls["n"] += 1
        return {"ok": True}

    ran = asyncio.run(ts.run_due_tasks(runner=runner))
    assert ran == []
    assert calls["n"] == 0
    with ts._LOCK:
        result = ts._load()[t["id"]]["result"]
        assert result.get("blocked") == "scheduler_ceiling"


def test_run_due_tasks_loads_synchronously_under_lock(monkeypatch, tmp_path):
    """Regression: run_due_tasks must call _load() synchronously under _LOCK.
    The pre-fix code did `with _LOCK: data = await asyncio.to_thread(_load)` —
    a threading.Lock held across an await point lets a concurrent sync caller
    (e.g. list_tasks from an API route on the same loop) block the entire event
    loop until the offload completes. The lock must never span an await."""
    monkeypatch.setattr(ts, "_TASKS_FILE", tmp_path / "tasks.json")
    t = ts.create_task("summarize my email inbox", "hourly", enabled=True)
    with ts._LOCK:
        data = ts._load()
        data[t["id"]]["last_run"] = (
            _dt.datetime.now() - _dt.timedelta(hours=2)
        ).isoformat()
        ts._save(data)

    class _FakeAsyncio:
        @staticmethod
        def to_thread(func, *args, **kwargs):
            raise AssertionError(
                f"run_due_tasks offloaded {getattr(func, '__name__', func)} via "
                "asyncio.to_thread — the threading _LOCK must never be held across an await"
            )

    monkeypatch.setattr(ts, "asyncio", _FakeAsyncio)

    calls = {"n": 0}

    async def runner(task):
        calls["n"] += 1
        return {"ok": True}

    ran = asyncio.run(ts.run_due_tasks(runner=runner))
    assert ran == [t["id"]]
    assert calls["n"] == 1


def test_daemon_heartbeat_written(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ts.TaskSchedulerDaemon, "_HEARTBEAT_FILE", tmp_path / "heartbeat.json"
    )
    daemon = ts.TaskSchedulerDaemon(interval_seconds=0.1)
    daemon._write_heartbeat()
    assert (tmp_path / "heartbeat.json").exists()
