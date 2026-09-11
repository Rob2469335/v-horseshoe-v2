"""/schedule must drive the REAL backend task scheduler (/control/tasks).

Regression: the CLI appended to SessionState.scheduled_tasks (a write-only
.session.json queue nothing consumed), so scheduled tasks silently never ran.
It now registers with the backend scheduler that the 60s daemon consumes.
"""

from __future__ import annotations

import io

from rich.console import Console

from organism_console._commands_dev import _resolve_schedule, cmd_schedule


class _Resp:
    def __init__(self, data, status=200):
        self._d = data
        self.status_code = status

    def json(self):
        return self._d


class _Ctx:
    def __init__(self, resp_map=None):
        self.console = Console(file=io.StringIO(), width=200)
        self.calls = []
        self._map = resp_map or {}

    def call_api(self, endpoint, method="GET", payload=None):
        self.calls.append((endpoint, method, payload))
        return self._map.get((method, endpoint), _Resp({}, 200))

    def out(self):
        return self.console.file.getvalue()


# ── schedule-token mapping ───────────────────────────────────────────────────
def test_seconds_maps_to_recurring_cron():
    sched, note = _resolve_schedule("300")
    assert sched == "*/5 * * * *"
    assert "recurring every 5 minute" in note


def test_60_seconds_is_one_minute():
    sched, note = _resolve_schedule("60")
    assert sched == "*/1 * * * *"
    assert "recurring every 1 minute" in note


def test_sub_60_seconds_rejected():
    sched, msg = _resolve_schedule("30")
    assert sched is None
    assert "60" in msg


def test_native_grammar_passes_through():
    assert _resolve_schedule("hourly")[0] == "hourly"
    assert _resolve_schedule("daily 08:00")[0] == "daily 08:00"
    assert _resolve_schedule("*/15 * * * *")[0] == "*/15 * * * *"


# ── add: POSTs to /control/tasks and echoes the RECURRING nature ─────────────
def test_add_posts_to_backend_and_echoes_recurring():
    ctx = _Ctx({("POST", "/control/tasks"): _Resp({"ok": True, "task": {"id": "abc"}})})
    cmd_schedule(ctx, ["300", "summarize", "my", "inbox"])
    ep, method, payload = ctx.calls[-1]
    assert ep == "/control/tasks" and method == "POST"
    assert payload == {
        "goal": "summarize my inbox",
        "schedule": "*/5 * * * *",
        "enabled": True,
    }
    out = ctx.out()
    assert "recurring every 5 minute" in out  # not just the raw cron
    assert "summarize my inbox" in out


def test_add_sub_60_rejected_without_posting():
    ctx = _Ctx()
    cmd_schedule(ctx, ["30", "do", "a", "thing"])
    assert ctx.calls == []  # nothing registered
    assert "60" in ctx.out()


def test_add_surfaces_ceiling_refusal():
    ctx = _Ctx(
        {
            ("POST", "/control/tasks"): _Resp(
                {"ok": False, "reason": "goal maps to an important action"}
            )
        }
    )
    cmd_schedule(ctx, ["hourly", "send an email to bob"])
    assert "refused" in ctx.out().lower()


# ── list / clear ─────────────────────────────────────────────────────────────
def test_list_renders_backend_tasks():
    ctx = _Ctx(
        {
            ("GET", "/control/tasks"): _Resp(
                {
                    "tasks": [
                        {
                            "id": "t1",
                            "goal": "check the news",
                            "schedule": "hourly",
                            "enabled": True,
                            "last_run": None,
                        }
                    ]
                }
            )
        }
    )
    cmd_schedule(ctx, ["list"])
    out = ctx.out()
    assert "check the news" in out and "hourly" in out
    assert ctx.calls[0] == ("/control/tasks", "GET", None)


def test_clear_deletes_each_task():
    ctx = _Ctx(
        {
            ("GET", "/control/tasks"): _Resp(
                {"tasks": [{"id": "a1"}, {"id": "a2"}]}
            )
        }
    )
    cmd_schedule(ctx, ["clear"])
    deletes = [c for c in ctx.calls if c[1] == "DELETE"]
    assert sorted(c[0] for c in deletes) == ["/control/tasks/a1", "/control/tasks/a2"]
    assert "Cleared 2" in ctx.out()


def test_call_api_supports_delete(monkeypatch):
    # Regression: call_api only handled GET/POST, so a DELETE was sent as POST
    # (405) and /schedule clear silently deleted nothing. This pins the branch.
    from organism_console import api_client

    seen = {}

    def _fake_delete(url, **kw):
        seen["url"] = url
        return object()

    monkeypatch.setattr(api_client.requests, "delete", _fake_delete)
    monkeypatch.setattr(api_client, "_auth_headers", lambda: {})
    api_client.call_api("/control/tasks/abc", "DELETE")
    assert seen["url"].endswith("/control/tasks/abc")

