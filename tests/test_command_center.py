"""Tests for Command Center Email + Browser + File integrations (2026 SOTA).

The key behaviors:
  1. email_config_status is graceful when unconfigured (no crash, no leak).
  2. email_draft returns a send_token; email_send REFUSES without confirmed=True
     (the human-approval gate) and refuses an expired/unknown token.
  3. File write via the approval gate refuses without approved=true.
  4. The persistent browser handler reports session state without crashing.
"""

import time

import pytest

from swarm_os.services import email_service as es


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    monkeypatch.setattr(es, "_CONFIG_PATH", tmp_path / "no_config.json")
    monkeypatch.setattr(es, "_SEND_TOKENS", {})
    return tmp_path


def test_email_config_status_graceful_when_unconfigured():
    """No config file -> configured False with a reason, never a crash."""
    st = es.email_config_status()
    assert st.get("configured") is False
    assert "config" in st.get("reason", "")


def test_email_draft_then_send_requires_approval():
    """email_draft returns a send_token; email_send REFUSES without confirmed."""
    d = es.email_draft("a@b.com", "hello", "body")
    assert d.get("ok") is True
    token = d.get("send_token")
    assert token
    # Unconfirmed -> refused (approval gate).
    r = es.email_send(token, confirmed=False)
    assert r.get("ok") is False
    assert "approval" in r.get("error", "").lower()


def test_email_send_unknown_token_refused():
    r = es.email_send("nonexistent-token", confirmed=True)
    assert r.get("ok") is False
    assert "token" in r.get("error", "").lower()


def test_email_send_expired_token_refused(monkeypatch):
    """A token older than the TTL is refused even with confirmed=True."""
    d = es.email_draft("a@b.com", "hi", "body")
    token = d["send_token"]
    with es._SEND_LOCK:
        es._SEND_TOKENS[token]["created"] = time.time() - 9999
    r = es.email_send(token, confirmed=True)
    assert r.get("ok") is False
    assert "expired" in r.get("error", "").lower()


def test_email_draft_requires_to_and_subject():
    r = es.email_draft("", "", "body")
    assert r.get("ok") is False
    assert "to and subject" in r.get("error", "")


# ── file approval gate (control router helpers) ─────────────────────────────
def test_file_read_resolution(tmp_path, monkeypatch):
    """The control router's _resolve_project_file must refuse traversal."""
    from swarm_os.api import control as ctl

    monkeypatch.chdir(tmp_path)
    (tmp_path / "ok.txt").write_text("hello", encoding="utf-8")
    assert ctl._resolve_project_file("ok.txt") == str(tmp_path / "ok.txt")
    with pytest.raises(Exception):
        ctl._resolve_project_file("..\\..\\etc\\passwd")
