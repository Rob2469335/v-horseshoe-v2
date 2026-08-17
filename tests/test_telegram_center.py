"""Tests for the Telegram command center (OpenClaw-gateway pattern, self-hosted).

All Telegram Bot API calls are mocked (httpx is never touched) — the identity
allowlist, command dispatch, approval bridge, and notify API are exercised
deterministically.
"""

import pytest

from swarm_os.core import settings as settings_mod
from swarm_os.services import approval_registry as ar
from swarm_os.services import telegram_center as tc


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "_center", None)
    ar._reset_registry()
    yield


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "12345")
    settings_mod.get_settings.cache_clear()
    yield
    settings_mod.get_settings.cache_clear()


def _mk_client(calls):
    """A fake TelegramClient that records calls instead of hitting the network."""

    class FakeClient:
        def __init__(self):
            self.base = "fake"
            self.sent = []

        async def aclose(self):
            pass

        async def get_updates(self, offset):
            return []

        async def send_message(
            self, chat_id, text, *, reply_markup=None, parse_mode="HTML"
        ):
            calls.append(
                {
                    "kind": "send_message",
                    "chat_id": chat_id,
                    "text": text,
                    "reply_markup": reply_markup,
                }
            )

        async def answer_callback(self, callback_id, text=""):
            calls.append(
                {"kind": "answer_callback", "callback_id": callback_id, "text": text}
            )

        async def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
            calls.append({"kind": "edit", "chat_id": chat_id, "message_id": message_id})

    return FakeClient()


# ── Identity allowlist ─────────────────────────────────────────────────────
def test_enabled_and_owner():
    assert tc.enabled() is True
    assert tc.is_owner(12345) is True
    assert tc.is_owner(99999) is False
    assert tc.is_owner("12345") is True  # numeric-string tolerant


def test_owner_unset_blocks_everyone(monkeypatch):
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "")
    settings_mod.get_settings.cache_clear()
    assert tc.is_owner(12345) is False


def test_disabled_when_no_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    settings_mod.get_settings.cache_clear()
    assert tc.enabled() is False
    assert tc.get_center()._client is None


# ── Command dispatch ───────────────────────────────────────────────────────
def test_non_owner_message_rejected():
    center = tc.get_center()
    calls = []
    center._client = _mk_client(calls)
    # Directly exercise _handle_update with a non-owner message.
    import asyncio

    async def run():
        await center._handle_update(
            {
                "update_id": 1,
                "message": {"chat": {"id": 999}, "from": {"id": 999}, "text": "/help"},
            }
        )

    asyncio.run(run())
    # The non-owner message was rejected with a "Not authorized" send.
    assert calls
    assert any(
        "Not authorized" in c["text"] for c in calls if c["kind"] == "send_message"
    )


def test_help_command_sends():
    center = tc.get_center()
    calls = []
    center._client = _mk_client(calls)
    import asyncio

    async def run():
        await center._handle_update(
            {
                "update_id": 1,
                "message": {
                    "chat": {"id": 12345},
                    "from": {"id": 12345},
                    "text": "/help",
                },
            }
        )

    asyncio.run(run())
    texts = [c["text"] for c in calls if c["kind"] == "send_message"]
    assert any("/status" in t for t in texts)


def test_status_command_uses_scheduler():
    center = tc.get_center()
    calls = []
    center._client = _mk_client(calls)
    import asyncio

    async def run():
        await center._handle_update(
            {
                "update_id": 1,
                "message": {
                    "chat": {"id": 12345},
                    "from": {"id": 12345},
                    "text": "/status",
                },
            }
        )

    asyncio.run(run())
    texts = [c["text"] for c in calls if c["kind"] == "send_message"]
    assert any("Command Center" in t for t in texts)


# ── Approval bridge ────────────────────────────────────────────────────────
def test_callback_approve_consumes_and_dispatches(monkeypatch):
    center = tc.get_center()
    calls = []
    center._client = _mk_client(calls)

    # Create a real pending action in the registry.
    registry = ar.get_registry()
    rec = registry.create(
        agent_id="t", turn=1, tool="filesystem", action="write", payload={"path": "/x"}
    )

    dispatched = {}

    async def fake_run(tool, payload):
        dispatched["tool"] = tool
        dispatched["payload"] = payload
        return {"ok": True}

    async def fake_dispatch(rec):
        await fake_run(rec["tool"], rec["payload"])

    center._dispatch_approved = fake_dispatch

    import asyncio

    async def run():
        await center._handle_callback(
            {
                "id": "cb1",
                "data": f"approve:{rec['pending_id']}",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 7},
            }
        )

    asyncio.run(run())
    assert dispatched.get("tool") == "filesystem"
    assert dispatched.get("payload") == {"path": "/x"}
    # The registry record was consumed (one-time use).
    assert registry.peek(rec["pending_id"]) is None


def test_dispatch_approved_executes_stored_payload_without_regate(monkeypatch):
    """The REAL _dispatch_approved seam (not the mock): a consumed pending action
    must dispatch its stored payload through _dispatch — NOT run(), which would
    re-apply the approval gate and silently mint a duplicate pending action
    instead of executing. Regression for the 2026-08-17 audit finding: the old
    `run(tool, payload)` never executed (authorization was fake)."""
    import asyncio
    from swarm_os.services.telegram_center import TelegramCommandCenter

    dispatched = {}

    async def fake_dispatch(tool, payload):
        dispatched["tool"] = tool
        dispatched["payload"] = payload
        return {"ok": True, "status": "executed"}

    monkeypatch.setattr(
        "runtime_v2.services.tool_executor._dispatch", fake_dispatch
    )
    center = TelegramCommandCenter()
    registry = ar.get_registry()
    rec = registry.create(
        agent_id="t", turn=1, tool="filesystem", action="write", payload={"path": "/x"}
    )
    consumed = registry.consume_any(rec["pending_id"])
    assert consumed is not None
    asyncio.run(center._dispatch_approved(consumed))
    assert dispatched.get("tool") == "filesystem"
    assert dispatched.get("payload") == {"path": "/x"}


def test_callback_deny_removes_pending():
    center = tc.get_center()
    calls = []
    center._client = _mk_client(calls)
    registry = ar.get_registry()
    rec = registry.create(
        agent_id="t", turn=1, tool="sandbox_repl", action=None, payload={"code": "x"}
    )
    import asyncio

    async def run():
        await center._handle_callback(
            {
                "id": "cb2",
                "data": f"deny:{rec['pending_id']}",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 8},
            }
        )

    asyncio.run(run())
    assert registry.peek(rec["pending_id"]) is None
    texts = [c["text"] for c in calls if c["kind"] == "answer_callback"]
    assert any("Denied" in t for t in texts)


def test_callback_unknown_pending():
    center = tc.get_center()
    calls = []
    center._client = _mk_client(calls)
    import asyncio

    async def run():
        await center._handle_callback(
            {
                "id": "cb3",
                "data": "approve:does-not-exist",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 9},
            }
        )

    asyncio.run(run())
    texts = [c["text"] for c in calls if c["kind"] == "answer_callback"]
    assert any("unknown or expired" in t for t in texts)


def test_callback_non_owner_rejected():
    center = tc.get_center()
    calls = []
    center._client = _mk_client(calls)
    import asyncio

    async def run():
        await center._handle_callback(
            {
                "id": "cb4",
                "data": "approve:x",
                "from": {"id": 99999},
                "message": {"chat": {"id": 99999}, "message_id": 10},
            }
        )

    asyncio.run(run())
    texts = [c["text"] for c in calls if c["kind"] == "answer_callback"]
    assert any("Not authorized" in t for t in texts)


# ── Notify API ─────────────────────────────────────────────────────────────
def test_notify_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    import asyncio

    assert asyncio.run(tc.notify("hello")) is False


def test_notify_sends_to_owner():
    center = tc.get_center()
    calls = []
    center._client = _mk_client(calls)
    tc._center = center
    import asyncio

    sent = asyncio.run(tc.notify("task done"))
    assert sent is True
    sent_messages = [c for c in calls if c["kind"] == "send_message"]
    assert sent_messages and sent_messages[0]["text"] == "task done"
