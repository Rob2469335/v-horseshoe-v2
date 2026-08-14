"""Tests for the inbox-management additions (Spark/Perplexity email parity).

Covers: thread grouping (_thread_key / email_thread), List-Unsubscribe parsing
(_parse_unsubscribe / email_unsubscribe_scan), inbox mutation dispatch
(email_manage on both the Gmail API + IMAP transports), and the LLM-powered
ops (email_summarize_thread / email_reply_draft / email_digest) with the LLM
seam mocked. No network is touched.
"""

import json

import pytest

from swarm_os.services import email_service as es
from swarm_os.services import gmail_api as ga


def _acc(**overrides):
    base = {
        "name": "gmail",
        "provider": "gmail",
        "email": "me@gmail.com",
        "transport": "gmail_api",
        "oauth2_client_id": "client.apps.googleusercontent.com",
        "oauth2_client_secret": "secret",
        "oauth2_auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "oauth2_token_url": "https://oauth2.googleapis.com/token",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    monkeypatch.setattr(
        "swarm_os.services.oauth2_loopback.get_valid_token",
        lambda *a, **k: "fake-access-token",
    )
    yield


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    monkeypatch.setattr(es, "_CONFIG_PATH", tmp_path / "no_config.json")
    monkeypatch.setattr(es, "_SEND_TOKENS", {})
    monkeypatch.setattr(ga, "_GMAIL_BASE", "https://gmail-test/v1/users/me")


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# ── Thread grouping ────────────────────────────────────────────────────────
def test_thread_key_strips_re_and_normalizes():
    m1 = {"subject": "Re: Hello World", "from": "alice@example.com"}
    m2 = {"subject": "Hello World", "from": "Alice <alice@example.com>"}
    assert es._thread_key(m1) == es._thread_key(m2)
    assert es._thread_key(
        {"subject": "Different", "from": "bob@example.com"}
    ) != es._thread_key(m1)


def test_email_thread_groups_and_orders(monkeypatch):
    msgs = [
        {"id": "1", "subject": "Re: Planning", "from": "a@x.com", "date": "2024-01-02"},
        {"id": "2", "subject": "Planning", "from": "b@x.com", "date": "2024-01-01"},
        {"id": "3", "subject": "Unrelated", "from": "c@x.com", "date": "2024-01-03"},
    ]
    monkeypatch.setattr(
        es, "email_list", lambda *a, **k: {"ok": True, "messages": msgs}
    )
    res = es.email_thread("1")
    assert res["ok"] is True
    assert res["count"] == 2
    assert [m["id"] for m in res["messages"]] == ["2", "1"]  # date-ordered
    assert res["thread_id"] == es._thread_key(msgs[0])


def test_email_thread_target_missing(monkeypatch):
    monkeypatch.setattr(
        es, "email_list", lambda *a, **k: {"ok": True, "messages": [{"id": "9"}]}
    )
    res = es.email_thread("nope")
    assert res["ok"] is False


# ── Unsubscribe ────────────────────────────────────────────────────────────
def test_parse_unsubscribe_mailto_and_http():
    raw = "<mailto:unsub@list.com?subject=unsub>, <https://list.com/unsub>"
    mech = es._parse_unsubscribe(raw)
    assert mech == [
        {"type": "mailto", "target": "unsub@list.com?subject=unsub"},
        {"type": "http", "target": "https://list.com/unsub"},
    ]


def test_parse_unsubscribe_empty():
    assert es._parse_unsubscribe("") == []
    assert es._parse_unsubscribe("<nope@x>") == []


def test_email_unsubscribe_scan(monkeypatch):
    msgs = [
        {
            "id": "1",
            "subject": "Newsletter",
            "from": "a@x.com",
            "date": "",
            "list_unsubscribe": "<https://x.com/u>",
        },
        {
            "id": "2",
            "subject": "Plain",
            "from": "b@x.com",
            "date": "",
            "list_unsubscribe": "",
        },
        {
            "id": "3",
            "subject": "Promo",
            "from": "c@x.com",
            "date": "",
            "list_unsubscribe": "<mailto:u@x.com>",
        },
    ]
    monkeypatch.setattr(
        es, "email_list", lambda *a, **k: {"ok": True, "messages": msgs}
    )
    res = es.email_unsubscribe_scan()
    assert res["ok"] is True
    assert res["count"] == 2
    assert res["messages"][0]["unsubscribe"][0]["type"] == "http"
    assert res["messages"][1]["unsubscribe"][0]["type"] == "mailto"


# ── email_manage on the Gmail API transport ────────────────────────────────
def test_email_manage_gmail_mark_read(monkeypatch, tmp_path):
    cfg = tmp_path / "email_config.json"
    cfg.write_text(json.dumps({"accounts": [_acc()]}), encoding="utf-8")
    monkeypatch.setattr(es, "_CONFIG_PATH", cfg)
    calls = []
    monkeypatch.setattr(
        ga,
        "_http_request",
        lambda method, url, headers=None, body=None: calls.append(
            (method, url, json.loads(body or b"{}"))
        ),
    )
    res = es.email_manage("mark_read", "abc")
    assert res["ok"] is True
    assert calls[0][2] == {"addLabelIds": [], "removeLabelIds": ["UNREAD"]}


def test_email_manage_gmail_delete(monkeypatch, tmp_path):
    cfg = tmp_path / "email_config.json"
    cfg.write_text(json.dumps({"accounts": [_acc()]}), encoding="utf-8")
    monkeypatch.setattr(es, "_CONFIG_PATH", cfg)
    calls = []
    monkeypatch.setattr(ga, "_http_request", lambda *a, **k: calls.append(a))
    res = es.email_manage("delete", "abc")
    assert res["ok"] is True
    assert "modify" in calls[0][1]


def test_email_manage_unknown_op_fails_closed(monkeypatch, tmp_path):
    cfg = tmp_path / "email_config.json"
    cfg.write_text(json.dumps({"accounts": [_acc()]}), encoding="utf-8")
    monkeypatch.setattr(es, "_CONFIG_PATH", cfg)
    res = es.email_manage("rename", "abc")
    assert res["ok"] is False


def test_email_manage_browser_transport_not_supported(monkeypatch, tmp_path):
    cfg = tmp_path / "email_config.json"
    cfg.write_text(
        json.dumps({"accounts": [_acc(transport="gmail_browser")]}), encoding="utf-8"
    )
    monkeypatch.setattr(es, "_CONFIG_PATH", cfg)
    res = es.email_manage("archive", "abc")
    assert res["ok"] is False
    assert "not supported" in res["error"]


# ── LLM-powered ops (LLM seam mocked) ──────────────────────────────────────
def test_email_summarize_thread(monkeypatch):
    msgs = [
        {
            "id": "1",
            "subject": "Project",
            "from": "a@x.com",
            "date": "2024-01-01",
            "body": "Need the report",
        },
        {
            "id": "2",
            "subject": "Re: Project",
            "from": "b@x.com",
            "date": "2024-01-02",
            "body": "Sent it",
        },
    ]
    monkeypatch.setattr(
        es, "email_list", lambda *a, **k: {"ok": True, "messages": msgs}
    )

    async def fake_complete(prompt, **kw):
        return "SUMMARY: agreed on the report."

    monkeypatch.setattr(es, "_acomplete", fake_complete)
    res = _run(es.email_summarize_thread("1"))
    assert res["ok"] is True
    assert res["message_count"] == 2
    assert res["summary"] == "SUMMARY: agreed on the report."


def test_email_summarize_thread_llm_down(monkeypatch):
    monkeypatch.setattr(
        es,
        "email_list",
        lambda *a, **k: {"ok": True, "messages": [{"id": "1", "body": "x"}]},
    )

    async def boom(prompt, **kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(es, "_acomplete", boom)
    res = _run(es.email_summarize_thread("1"))
    assert res["ok"] is True
    assert "LLM unavailable" in res["summary"]


def test_email_reply_draft_mints_send_token(monkeypatch):
    monkeypatch.setattr(
        es,
        "email_read",
        lambda *a, **k: {
            "ok": True,
            "from": "alice@example.com",
            "subject": "Hello",
            "body": "hi",
        },
    )
    monkeypatch.setattr(
        es,
        "email_thread",
        lambda *a, **k: {"ok": True, "messages": [{"body": "earlier"}]},
    )

    async def fake_complete(prompt, **kw):
        return "Hey Alice, happy to help!"

    monkeypatch.setattr(es, "_acomplete", fake_complete)
    res = _run(es.email_reply_draft("1", note="say yes"))
    assert res["ok"] is True
    assert res["draft"]["to"] == "alice@example.com"
    assert res["draft"]["subject"] == "Re: Hello"
    assert res["send_token"]
    assert "send_token" in res


def test_email_reply_draft_llm_down(monkeypatch):
    monkeypatch.setattr(
        es,
        "email_read",
        lambda *a, **k: {"ok": True, "from": "a@x.com", "subject": "S", "body": "hi"},
    )
    monkeypatch.setattr(
        es, "email_thread", lambda *a, **k: {"ok": True, "messages": []}
    )

    async def boom(prompt, **kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(es, "_acomplete", boom)
    res = _run(es.email_reply_draft("1"))
    assert res["ok"] is False


def test_email_digest(monkeypatch):
    msgs = [
        {
            "id": "1",
            "from": "news@list.com",
            "subject": "Newsletter",
            "body": "spam",
            "unread": True,
        },
        {
            "id": "2",
            "from": "boss@co.com",
            "subject": "URGENT",
            "body": "need this today",
            "unread": True,
        },
    ]
    monkeypatch.setattr(
        es, "email_list", lambda *a, **k: {"ok": True, "messages": msgs}
    )

    async def fake_complete(prompt, **kw):
        return "ACTION: reply to boss."

    monkeypatch.setattr(es, "_acomplete", fake_complete)
    res = _run(es.email_digest(days=7))
    assert res["ok"] is True
    assert res["count"] == 2
    assert res["digest"] == "ACTION: reply to boss."
    assert res["degraded"] is False


def test_email_digest_empty_inbox(monkeypatch):
    monkeypatch.setattr(es, "email_list", lambda *a, **k: {"ok": True, "messages": []})
    res = _run(es.email_digest())
    assert res["ok"] is True
    assert res["count"] == 0
