"""Tests for the Gmail REST API transport (HTTPS:443, no IMAP/SMTP).

This is the path agents (Gemini/Claude-style) use when SMTP/IMAP ports are
blocked or MITM'd on the network (e.g. AV "mail shield" TLS interception that
rewrites/resets 587/993 while HTTPS:443 stays clean).

All tests drive the REAL gmail_api HTTP/thunk seams through mocked `_http_request`
(never touching the network) and a mocked OAuth token. The approval-gate
contract (email_draft -> send_token -> email_send confirmed-only send, carried
by email_service) is preserved end-to-end through the gmail_api send leg.
"""
import base64
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


# ── Gmail message parsing ──────────────────────────────────────────────────
def _gmail_headers(**h):
    return [{"name": k, "value": v} for k, v in h.items()]


def test_msg_from_gmail_metadata_no_body():
    m = {
        "id": "abc",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": _gmail_headers(Subject="Hello", From="boss@x.com",
                                      To="me@gmail.com", Date="Tue, 1 Jan 2026 00:00:00 +0000"),
            "mimeType": "text/plain",
            "body": {"data": ""},
        },
    }
    p = ga._msg_from_gmail(m, with_body=False)
    assert p["subject"] == "Hello"
    assert p["from"] == "boss@x.com"
    assert p["date"].startswith("Tue")
    assert p["body"] == ""


def test_msg_from_gmail_full_body_and_attachment_count():
    payload = {"data": base64.urlsafe_b64encode(b"plain body").decode()}
    m = {
        "id": "abc",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": _gmail_headers(Subject="Hi"),
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": payload},
                {"mimeType": "application/pdf", "filename": "report.pdf",
                 "body": {"data": base64.urlsafe_b64encode(b"%PDF").decode()}},
            ],
        },
    }
    p = ga._msg_from_gmail(m, with_body=True)
    assert p["body"] == "plain body"
    assert p["attachments"] == 1


# ── read ops ───────────────────────────────────────────────────────────────
def test_gmail_list_returns_metadata(monkeypatch):
    calls = {}

    def fake(method, url, headers=None, body=None):
        calls.setdefault("url", []).append(url)
        if "/messages?" in url:
            return {"messages": [{"id": "m1"}, {"id": "m2"}]}
        return {"id": "m1", "labelIds": ["INBOX"],
                "payload": {"headers": _gmail_headers(Subject="S1", From="a@b", To="me", Date="d"),
                            "mimeType": "text/plain"}}

    monkeypatch.setattr(ga, "_http_request", fake)
    data = ga.gmail_list(_acc(), limit=2)
    assert data["ok"] is True
    assert data["count"] == 2
    assert data["messages"][0]["subject"] == "S1"
    assert any("format=metadata" in u for u in calls["url"])


def test_gmail_list_unread_only_adds_q(monkeypatch):
    seen = {}

    def fake(method, url, headers=None, body=None):
        seen["url"] = url
        return {"messages": []}

    monkeypatch.setattr(ga, "_http_request", fake)
    ga.gmail_list(_acc(), unread_only=True)
    assert "q=is%3Aunread" in seen["url"]


def test_gmail_read_full_body(monkeypatch):
    def fake(method, url, headers=None, body=None):
        assert "format=full" in url
        return {"id": "m1", "labelIds": ["INBOX"],
                "payload": {"headers": _gmail_headers(Subject="Full", From="a@b", To="me", Date="d"),
                            "mimeType": "text/plain",
                            "body": {"data": base64.urlsafe_b64encode(b"the whole body").decode()}}}

    monkeypatch.setattr(ga, "_http_request", fake)
    r = ga.gmail_read(_acc(), uid="m1")
    assert r["ok"] is True
    assert r["body"] == "the whole body"


def test_gmail_search_passes_query(monkeypatch):
    urls = []

    def fake(method, url, headers=None, body=None):
        urls.append(url)
        if "/messages?" in url:
            return {"messages": [{"id": "m1"}]}
        return {"id": "m1", "labelIds": [],
                "payload": {"headers": _gmail_headers(Subject="Match", From="a@b", To="me", Date="d"),
                            "mimeType": "text/plain"}}

    monkeypatch.setattr(ga, "_http_request", fake)
    r = ga.gmail_search(_acc(), query="from:boss budget")
    assert r["ok"] is True
    assert any("q=from%3Aboss+budget" in u for u in urls)
    assert r["messages"][0]["subject"] == "Match"


def test_gmail_read_not_found(monkeypatch):
    monkeypatch.setattr(ga, "_http_request", lambda *a, **k: None)
    r = ga.gmail_read(_acc(), uid="nope")
    assert r["ok"] is False
    assert "not found" in r.get("error", "")


def test_gmail_http_error_flattened(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Gmail API HTTP 403: <html>secret detail</html>")

    monkeypatch.setattr(ga, "_http_request", boom)
    r = ga.gmail_list(_acc())
    assert r["ok"] is False
    assert "403" in r.get("error", "")


def test_gmail_no_token_returns_error(monkeypatch):
    monkeypatch.setattr("swarm_os.services.oauth2_loopback.get_valid_token", lambda *a, **k: None)
    r = ga.gmail_list(_acc())
    assert r["ok"] is False
    assert "token" in r.get("error", "").lower()


# ── send (approval-gate preserved end-to-end) ──────────────────────────────
def test_email_send_via_gmail_api_requires_approval(monkeypatch, tmp_path):
    """The human-approval gate stays authoritative even on the gmail_api leg:
    unconfirmed token is REFUSED, and the confirmed send goes to the Gmail API
    (never an SMTP socket)."""
    cfg = tmp_path / "email_config.json"
    cfg.write_text(json.dumps({"accounts": [_acc()]}), encoding="utf-8")
    monkeypatch.setattr(es, "_CONFIG_PATH", cfg)

    sent = {}

    def fake(method, url, headers=None, body=None):
        if method == "POST":
            sent["url"] = url
            payload = json.loads(body.decode())
            assert "raw" in payload
            decoded = base64.urlsafe_b64decode(payload["raw"] + "==")
            assert b"Subject: Hello Gmail" in decoded
            return {"id": "sent-1", "threadId": "t-1"}
        return {"messages": []}

    monkeypatch.setattr(ga, "_http_request", fake)

    d = es.email_draft("them@gmail.com", "Hello Gmail", "body text", account="gmail")
    token = d["send_token"]
    unconfirmed = es.email_send(token, confirmed=False)
    assert unconfirmed["ok"] is False
    assert "approval" in unconfirmed["error"].lower()
    assert "url" not in sent  # nothing dispatched before approval

    r = es.email_send(token, confirmed=True)
    assert r["ok"] is True
    assert sent["url"].endswith("/users/me/messages/send")
    assert r["id"] == "sent-1"


def test_email_send_via_gmail_api_reuses_token_once(monkeypatch, tmp_path):
    cfg = tmp_path / "email_config.json"
    cfg.write_text(json.dumps({"accounts": [_acc()]}), encoding="utf-8")
    monkeypatch.setattr(es, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(ga, "_http_request",
                        lambda m, u, headers=None, body=None: {"id": "x"} if m == "POST" else {"messages": []})
    token = es.email_draft("them@gmail.com", "Hi", "b", account="gmail")["send_token"]
    assert es.email_send(token, confirmed=True)["ok"] is True
    assert es.email_send(token, confirmed=True)["ok"] is False  # one-time consume


# ── email_service transport dispatch ───────────────────────────────────────
def test_email_list_dispatches_to_gmail_api(monkeypatch, tmp_path):
    cfg = tmp_path / "email_config.json"
    cfg.write_text(json.dumps({"accounts": [_acc()]}), encoding="utf-8")
    monkeypatch.setattr(es, "_CONFIG_PATH", cfg)
    called = []
    monkeypatch.setattr(ga, "gmail_list", lambda acc, **kw: called.append((acc, kw)) or {"ok": True})
    r = es.email_list(account="gmail")
    assert r == {"ok": True}
    assert called and called[0][1]["unread_only"] is False


def test_email_config_status_reports_transport(monkeypatch, tmp_path):
    cfg = tmp_path / "email_config.json"
    cfg.write_text(json.dumps({"accounts": [_acc()]}), encoding="utf-8")
    monkeypatch.setattr(es, "_CONFIG_PATH", cfg)
    st = es.email_config_status()
    assert st["configured"] is True
    assert st["transport"] == "gmail_api"


def test_imap_account_still_default_transport():
    """Accounts WITHOUT transport:gmail_api keep IMAP semantics — the config
    status must report 'imap', not gmail_api."""
    from pathlib import Path
    p = Path("config/email_config.example.json")
    raw = json.loads(p.read_text())
    assert raw["accounts"][0].get("transport", "imap") == "imap"
    assert raw["accounts"][1]["transport"] == "gmail_api"