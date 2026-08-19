"""Tests for the IMAP transport in email_service.

Covers the 2026-08-19 audit round: the IMAP ops must (a) treat the message
identifier produced/consumed by list/read/search/manage as a UID (stable across
connections), NOT a sequence number; (b) list without marking messages \\Seen and
report real unread state; (c) give every IMAP socket a timeout; (d) return real
bodies from search. No network is touched — a FakeIMAP mimics the imaplib surface.
"""

import json

import pytest

from swarm_os.services import email_service as es


def _imap_acc(**overrides):
    base = {
        "name": "imap",
        "provider": "imap",
        "email": "me@example.com",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "smtp_host": "smtp.example.com",
        "user": "me@example.com",
        "app_password": "secret",
    }
    base.update(overrides)
    return base


def _write_cfg(tmp_path, monkeypatch, acc=None):
    cfg = tmp_path / "email_config.json"
    cfg.write_text(
        json.dumps({"accounts": [acc or _imap_acc()]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(es, "_CONFIG_PATH", cfg)


def _raw(subject="URGENT", body="need this today", seen=True):
    return (
        "From: Alice <alice@example.com>\r\n"
        "To: me@example.com\r\n"
        "Date: Tue, 05 Mar 2024 10:00:00 +0000\r\n"
        f"Subject: {subject}\r\n"
        "Message-ID: <abc@example.com>\r\n"
        "\r\n"
        f"{body}"
    ).encode()


class FakeIMAP:
    """Mimics the imaplib surface the IMAP transport depends on.

    Deliberately exposes ONLY the UID command forms (uid("SEARCH"/"FETCH"/
    "STORE"/"COPY")) plus select/login/logout/expunge. It has NO raw
    `search`/`fetch`/`store`/`copy` attributes, so a regression back to
    sequence-number ops fails with AttributeError instead of silently passing.
    """

    def __init__(self, msgs):
        # msgs: list of dicts {"raw": bytes, "seen": bool}
        self._msgs = msgs
        self.commands = []  # every uid command as (CMD, args)
        self.fetches = []  # (uid, spec) seen under uid("FETCH", ...)

    def login(self, user, password):
        return ("OK", [b""])

    def select(self, folder):
        return ("OK", [b"%d" % len(self._msgs)])

    def uid(self, command, *args):
        self.commands.append((command, args))
        cmd = command.upper()
        if cmd == "SEARCH":
            return (
                "OK",
                [b" ".join(str(i + 1).encode() for i in range(len(self._msgs)))],
            )
        if cmd == "FETCH":
            uid = int(args[0])
            uid = str(uid)
            spec = args[1].decode() if isinstance(args[1], bytes) else str(args[1])
            self.fetches.append((uid, spec))
            idx = int(uid) - 1
            if idx < 0 or idx >= len(self._msgs):
                return ("NO", [b"invalid sequence number"])
            raw = self._msgs[idx]["raw"]
            flag = b"\\Seen" if self._msgs[idx].get("seen") else b"\\Recent"
            header = b"%d (FLAGS (%s) BODY[] {%d}" % (int(uid), flag, len(raw))
            return ("OK", [(header, raw), b")"])
        if cmd in ("STORE", "COPY"):
            return ("OK", [b""])
        raise ValueError(f"unexpected uid command: {command} {args}")

    def expunge(self):
        return ("OK", [b""])

    def logout(self):
        return ("OK", [b"BYE"])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # No config by default (email disabled); tests opt in via _write_cfg.
    monkeypatch.setattr(es, "_CONFIG_PATH", tmp_path / "no_config.json")
    monkeypatch.setattr(es, "_SEND_TOKENS", {})
    yield


# ── UID stability (audit: sequence numbers sued as stable ids) ─────────────
def test_email_list_searches_and_fetches_by_uid(monkeypatch, tmp_path):
    _write_cfg(tmp_path, monkeypatch)
    fake = FakeIMAP([{"raw": _raw(), "seen": True}])
    monkeypatch.setattr(es, "_get_imap", lambda acc: fake)

    res = es.email_list(limit=10)

    assert res["ok"] is True
    # The ONLY mapping/searches must be UID-shaped (no raw search/fetch attr).
    cmds = [c[0] for c in fake.commands]
    assert "SEARCH" in cmds
    assert "FETCH" in cmds
    assert res["messages"][0]["id"] == "1"


def test_email_read_fetches_by_uid_on_fresh_connection(monkeypatch, tmp_path):
    _write_cfg(tmp_path, monkeypatch)
    fake = FakeIMAP([{"raw": _raw(), "seen": True}])
    monkeypatch.setattr(es, "_get_imap", lambda acc: fake)

    res = es.email_read("1")

    assert res["ok"] is True
    assert fake.fetches and str(fake.fetches[0][0]) == "1"
    assert res["subject"] == "URGENT"


def test_email_search_scans_by_uid(monkeypatch, tmp_path):
    _write_cfg(tmp_path, monkeypatch)
    fake = FakeIMAP(
        [
            {
                "raw": _raw(subject="URGENT", body="need this today", seen=True),
                "seen": True,
            },
            {"raw": _raw(subject="Other", body="unrelated", seen=True), "seen": True},
        ]
    )
    monkeypatch.setattr(es, "_get_imap", lambda acc: fake)

    res = es.email_search("URGENT")

    assert res["ok"] is True
    assert res["count"] == 1
    assert res["messages"][0]["id"] == "1"


def test_email_manage_flags_and_copies_by_uid(monkeypatch, tmp_path):
    _write_cfg(tmp_path, monkeypatch)
    fake = FakeIMAP([{"raw": _raw(), "seen": True}])
    monkeypatch.setattr(es, "_get_imap", lambda acc: fake)

    res = es.email_manage("mark_read", "1")
    assert res["ok"] is True
    assert ("STORE", ("1", "+FLAGS", r"(\Seen)")) in fake.commands

    fake2 = FakeIMAP([{"raw": _raw(), "seen": True}])
    monkeypatch.setattr(es, "_get_imap", lambda acc: fake2)
    res = es.email_manage("archive", "1", target_folder="[Gmail]/All Mail")
    assert res["ok"] is True
    assert ("COPY", ("1", "[Gmail]/All Mail")) in fake2.commands
    assert ("STORE", ("1", "+FLAGS", r"(\Deleted)")) in fake2.commands


# ── list must not mark read + real unread state (audit) ────────────────────
def test_email_list_uses_body_peek_not_rfc822(monkeypatch, tmp_path):
    _write_cfg(tmp_path, monkeypatch)
    fake = FakeIMAP([{"raw": _raw(), "seen": True}])
    monkeypatch.setattr(es, "_get_imap", lambda acc: fake)

    res = es.email_list(limit=10)

    assert res["ok"] is True
    # A LIST must not flip \\Seen on the server: (RFC822) == (BODY[]) which
    # marks read; BODY.PEEK[] never touches the flags.
    assert fake.fetches and fake.fetches[0][1] == "(FLAGS BODY.PEEK[])"


def test_email_list_reports_real_unread_state(monkeypatch, tmp_path):
    _write_cfg(tmp_path, monkeypatch)
    fake = FakeIMAP(
        [
            {"raw": _raw(subject="Seen one", seen=True), "seen": True},
            {"raw": _raw(subject="Fresh two", seen=False), "seen": False},
        ]
    )
    monkeypatch.setattr(es, "_get_imap", lambda acc: fake)

    res = es.email_list(limit=10)

    assert res["ok"] is True
    by_subject = {m["subject"]: m for m in res["messages"]}
    assert by_subject["Seen one"]["unread"] is False
    assert by_subject["Fresh two"]["unread"] is True


# ── IMAP socket timeout (audit) ──────────────────────────────────────────
def test_email_imap_timeout_configured(monkeypatch, tmp_path):
    _write_cfg(tmp_path, monkeypatch)
    captured = {}

    import swarm_os.services.email_service as mod

    original_imap = mod.imaplib.IMAP4_SSL

    def capture_timeout(host, port, ssl_context=None, timeout=None, **kwargs):
        captured["timeout"] = timeout
        # Return a minimal fake connection that supports the operations
        # the test needs (login, select, uid, logout).
        # We'll just return a FakeIMAP instance that behaves like a connection.
        return FakeIMAP([{"raw": _raw(), "seen": True}])

    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", capture_timeout)

    res = es.email_list(limit=10)
    assert res["ok"] is True
    assert captured.get("timeout") == 30
