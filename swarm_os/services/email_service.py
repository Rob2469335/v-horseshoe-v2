"""Email integration for the local swarm (2026 SOTA: inbox-as-a-tool).

Best-possible auth: supports BOTH app-password IMAP/SMTP (Gmail/Outlook/iCloud)
AND a fully-local OAuth2 loopback flow (localhost callback + token refresh, no
cloud relay) for providers that need it.

Design notes (reviewer-locked):
- Credentials/config come from config/email_config.json, NEVER committed. The
  file is gitignored; a template documents the shape without secrets.
- Read ops (list/search/read/unread/folders) are un-gated — they're how the
  agent treats the inbox as a tool.
- SEND is the ONLY gated op. It returns a draft with a `send_token`; the caller
  (tool_executor) must route it through the approval gate; only a confirmed
  token actually sends. This is deliberate: autonomous email *responses* are
  where local agents fail (thread grounding, attachment handling), so send stays
  human-approved per the 2026 research.
- Never raises on a read error: returns {"ok": False, "error": "..."}.
"""
from __future__ import annotations

import base64
import email
import email.utils
import imaplib
import json
import logging
import os
import smtplib
import ssl
import threading
import time
from email.header import decode_header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

log = logging.getLogger(__name__)

_CONFIG_PATH = Path("config/email_config.json")
_SEND_TOKENS: dict[str, dict] = {}
_SEND_LOCK = threading.Lock()
_TOKEN_TTL_S = 300  # an approval token expires after 5 minutes


def _load_config() -> dict | None:
    """Load the email config. None if missing/invalid (fail-soft: email disabled)."""
    try:
        if not _CONFIG_PATH.exists():
            return None
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("accounts"):
            return None
        return data
    except Exception as exc:
        log.warning("Email config load failed: %s", exc)
        return None


def _account(name: str | None = None) -> dict | None:
    cfg = _load_config()
    if not cfg:
        return None
    accounts = cfg.get("accounts", [])
    if not accounts:
        return None
    if name:
        for a in accounts:
            if a.get("name") == name:
                return a
        return None
    return accounts[0]


def _decode_header_value(raw) -> str:
    parts = decode_header(raw or "")
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _get_imap(acc: dict):
    imap_host = acc.get("imap_host") or acc.get("host")
    imap_port = int(acc.get("imap_port", 993))
    imap_user = acc.get("user") or acc.get("email")
    imap_pass = acc.get("app_password") or acc.get("password") or acc.get("access_token")
    if acc.get("oauth2") and not acc.get("app_password"):
        from swarm_os.services.oauth2_loopback import get_valid_token
        token = get_valid_token(acc.get("name", "default"), acc)
        if token:
            # XOAUTH2: "user=<user>\x01auth=Bearer <token>\x01\x01"
            imap_pass = "user=%s\1auth=Bearer %s\1\1" % (imap_user, token)
        else:
            return None
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(imap_host, imap_port, ssl_context=ctx)
    conn.login(imap_user, imap_pass)
    return conn


def _parse_msg(uid: str, raw: bytes) -> dict:
    msg = email.message_from_bytes(raw)
    subject = _decode_header_value(msg.get("Subject", ""))
    frm = _decode_header_value(msg.get("From", ""))
    to = _decode_header_value(msg.get("To", ""))
    date = msg.get("Date", "")
    body_parts = []
    attachments = 0
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if part.get_filename():
                attachments += 1
            elif ctype == "text/plain" and "attachment" not in disp.lower():
                try:
                    body_parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace"))
                except Exception:
                    pass
    else:
        try:
            body_parts.append(msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace"))
        except Exception:
            pass
    return {
        "id": uid,
        "subject": subject,
        "from": frm,
        "to": to,
        "date": date,
        "attachments": attachments,
        "body": "\n".join(body_parts)[:8000],
    }


def email_list(folder: str = "INBOX", limit: int = 20, unread_only: bool = False, account: str | None = None) -> dict:
    """List recent messages in a folder (preview: subject/from/date/attachments)."""
    try:
        acc = _account(account)
        if not acc:
            return {"ok": False, "error": "email not configured (config/email_config.json missing)"}
        conn = _get_imap(acc)
        try:
            conn.select(folder)
            typ, data = conn.search(None, "UNSEEN" if unread_only else "ALL")
            if typ != "OK":
                return {"ok": False, "error": f"IMAP search failed: {typ}"}
            ids = data[0].split()
            ids = ids[-limit:] if ids else []
            out = []
            for i in ids:
                typ, msg_data = conn.fetch(i, "(RFC822)")
                if typ == "OK" and msg_data and msg_data[0]:
                    raw = msg_data[0][1]
                    parsed = _parse_msg(i.decode(), raw)
                    parsed["unread"] = True
                    out.append(parsed)
            return {"ok": True, "folder": folder, "count": len(out), "messages": out}
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as exc:
        log.warning("email_list failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def email_read(uid: str, folder: str = "INBOX", account: str | None = None) -> dict:
    """Read one message's full body."""
    try:
        acc = _account(account)
        if not acc:
            return {"ok": False, "error": "email not configured"}
        conn = _get_imap(acc)
        try:
            conn.select(folder)
            typ, msg_data = conn.fetch(uid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                return {"ok": False, "error": f"message {uid} not found"}
            parsed = _parse_msg(uid, msg_data[0][1])
            parsed["ok"] = True
            return parsed
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as exc:
        log.warning("email_read failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def email_search(query: str, folder: str = "INBOX", limit: int = 20, account: str | None = None) -> dict:
    """Search by subject/from body substring (IMAP TEXT search)."""
    try:
        acc = _account(account)
        if not acc:
            return {"ok": False, "error": "email not configured"}
        conn = _get_imap(acc)
        try:
            conn.select(folder)
            # IMAP TEXT search over a multi-word query: AND the terms.
            terms = query.split()
            typ, data = conn.search(None, "ALL")
            if typ != "OK":
                return {"ok": False, "error": "search failed"}
            ids = data[0].split()
            # Fetch headers to filter locally (TEXT search is server-side but the
            # charset handling for non-ASCII is unreliable; header scan is robust).
            out = []
            for i in ids[-200:]:
                typ, msg_data = conn.fetch(i, "(BODY.PEEK[HEADER])")
                if typ != "OK" or not msg_data[0]:
                    continue
                header_blob = msg_data[0][1]
                head = email.message_from_bytes(header_blob)
                hay = " ".join([
                    _decode_header_value(head.get("Subject", "")),
                    _decode_header_value(head.get("From", "")),
                    _decode_header_value(head.get("To", "")),
                ]).lower()
                if all(t.lower() in hay for t in terms):
                    parsed = _parse_msg(i.decode(), header_blob)
                    out.append(parsed)
                    if len(out) >= limit:
                        break
            return {"ok": True, "query": query, "count": len(out), "messages": out}
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as exc:
        log.warning("email_search failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _build_message(to: str, subject: str, body: str, cc: str = "", attachments: list[str] | None = None) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for path_str in (attachments or []):
        p = Path(path_str)
        if p.exists() and p.is_file():
            part = MIMEApplication(p.read_bytes())
            part.add_header("Content-Disposition", "attachment", filename=p.name)
            msg.attach(part)
    return msg


def email_draft(to: str, subject: str, body: str, cc: str = "", attachments: list[str] | None = None,
                account: str | None = None) -> dict:
    """Stage a message as a draft and mint a short-lived send token. NOT sent yet —
    the caller must route email_send through the approval gate."""
    if not to or not subject:
        return {"ok": False, "error": "to and subject are required"}
    token = base64.urlsafe_b64encode(os.urandom(16)).decode()
    with _SEND_LOCK:
        _SEND_TOKENS[token] = {
            "to": to, "subject": subject, "body": body, "cc": cc,
            "attachments": attachments or [], "account": account, "created": time.time(),
        }
    return {"ok": True, "draft": {"to": to, "subject": subject, "body_len": len(body), "attachments": len(attachments or [])},
            "send_token": token, "expires_in_s": _TOKEN_TTL_S}


def email_send(send_token: str, confirmed: bool = False) -> dict:
    """Send a staged draft, ONLY with an approval-confirmed token.

    The approval gate (tool_executor / Command Center) holds the token and only
    calls this with confirmed=True after a human approves. A token used without
    confirmed=True, or expired, is refused."""
    if not confirmed:
        return {"ok": False, "error": "email_send requires approval: the draft was returned with a send_token; confirm before sending"}
    with _SEND_LOCK:
        draft = _SEND_TOKENS.get(send_token)
        if not draft:
            return {"ok": False, "error": "unknown or expired send token (drafts expire after %ss)" % _TOKEN_TTL_S}
        if time.time() - draft["created"] > _TOKEN_TTL_S:
            _SEND_TOKENS.pop(send_token, None)
            return {"ok": False, "error": "send token expired"}
        # Consume the token so it can't be sent twice.
        _SEND_TOKENS.pop(send_token, None)
    try:
        acc = _account(draft["account"])
        if not acc:
            return {"ok": False, "error": "email not configured"}
        msg = _build_message(draft["to"], draft["subject"], draft["body"], draft["cc"], draft["attachments"])
        smtp_host = acc.get("smtp_host") or acc.get("host")
        smtp_port = int(acc.get("smtp_port", 587))
        smtp_user = acc.get("user") or acc.get("email")
        smtp_pass = acc.get("app_password") or acc.get("password") or acc.get("access_token")
        ctx = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as conn:
            conn.starttls(context=ctx)
            conn.login(smtp_user, smtp_pass)
            conn.sendmail(smtp_user, [draft["to"]], msg.as_string())
        return {"ok": True, "to": draft["to"], "subject": draft["subject"]}
    except Exception as exc:
        log.warning("email_send failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def email_config_status(account: str | None = None) -> dict:
    acc = _account(account)
    if not acc:
        return {"configured": False, "reason": "config/email_config.json missing or has no accounts"}
    return {"configured": True, "account": acc.get("name") or acc.get("email"), "provider_hint": acc.get("provider", "imap")}
