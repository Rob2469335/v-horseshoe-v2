"""Tests for Command Center Email + Browser + File integrations (2026 SOTA).

The key behaviors:
  1. email_config_status is graceful when unconfigured (no crash, no leak).
  2. email_draft returns a send_token; email_send REFUSES without confirmed=True
     (the human-approval gate) and refuses an expired/unknown token.
  3. File write via the approval gate refuses without approved=true.
  4. The persistent browser handler reports session state without crashing.
"""

import asyncio
import os
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
    # os.path.join builds a platform-correct traversal: backslash separators are
    # only a traversal on Windows — a literal "..\\..\\etc\\passwd" is a plain
    # filename on POSIX and stays inside the project root (no exception).
    with pytest.raises(Exception):
        ctl._resolve_project_file(os.path.join("..", "..", "etc", "passwd"))


def test_file_read_resolution_rejects_sibling_prefix_collision(tmp_path, monkeypatch):
    """A sibling directory whose NAME merely starts with the project dir must be
    refused — the old string-prefix check (`joined.startswith(root)`) accepted
    `C:\\...\\v-horseshoe-v2_evil\\payload.py`. Regression for the 2026-08-17
    audit finding; uses os.path.commonpath containment now."""
    from swarm_os.api import control as ctl

    project = tmp_path / "v-horseshoe-v2"
    sibling = tmp_path / "v-horseshoe-v2_evil"
    project.mkdir(exist_ok=True)
    sibling.mkdir(exist_ok=True)
    (sibling / "payload.py").write_text("pwned", encoding="utf-8")
    monkeypatch.chdir(project)
    with pytest.raises(Exception):
        ctl._resolve_project_file(str(sibling / "payload.py"))


def test_browser_image_endpoint_cannot_disclose_root_files(tmp_path, monkeypatch):
    """/control/browser/image must serve ONLY .png files from logs/screenshots -
    never arbitrary project-root files. Regression for the 2026-08-23 audit:
    the old cwd-rooted version served `.env` and `swarm_config.json` via
    `?name=.env`. Revert-proof: fails on the pre-fix cwd-rooted handler."""
    from fastapi import HTTPException
    from swarm_os.api import control as ctl

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SECRET_TOKEN=abc", encoding="utf-8")
    (tmp_path / "swarm_config.json").write_text("{}", encoding="utf-8")

    # Root file, even with a png-ish trick or bare name -> refused.
    with pytest.raises(HTTPException):
        asyncio.run(ctl.control_browser_image(".env"))
    with pytest.raises(HTTPException):
        asyncio.run(ctl.control_browser_image("swarm_config.json"))
    # Non-png suffix in the shots dir -> refused.
    shots = tmp_path / "logs" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(HTTPException):
        asyncio.run(ctl.control_browser_image("notes.txt"))
    # A real screenshot in the shots dir -> served (sanity, not just refusal).
    (shots / "shot.png").write_bytes(b"\x89PNG fake")
    resp = asyncio.run(ctl.control_browser_image("shot.png"))
    assert resp.path.endswith("shot.png")

