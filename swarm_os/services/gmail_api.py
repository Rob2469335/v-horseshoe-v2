"""Gmail REST API transport for the email tool (2026 SOTA: no IMAP/SMTP).

Selected per-account when config/email_config.json sets `"transport":
"gmail_api"` on an account. This is the path Gemini/Claude-style agents use:
everything rides gmail.googleapis.com over HTTPS:443 with a Bearer access token
from the fully-local OAuth2 loopback (swarm_os.services.oauth2_loopback) — no
app password, no SMTP/IMAP ports (587/993), so AV "mail shield" TLS
interception (which rewrites / resets those ports) is never in the path.

Design notes (reviewer-locked):
- Read ops (list/read/search) are un-gated — the same contract as the IMAP
  transport.
- SEND goes through the SAME email_draft -> send_token -> email_send approval
  gate in email_service. This module only replaces the transport leg:
  email_service builds the MIME, then calls gmail_send_mime() with the bytes.
- Never raises: every public function returns {"ok": False, "error": ...} on
  failure (urllib HTTPError is caught and flattened into the error string).
- Auth is delegated to oauth2_loopback.get_valid_token(account_name, acc) —
  browser consent on a local loopback, tokens persisted to
  config/.email_tokens/ and auto-refreshed. Requires the Google OAuth client
  (client id/secret) created in Google Cloud Console for a "Desktop app".
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_MAX_PAGE = 100


def _http_request(
    method: str, url: str, headers: dict | None = None, body: bytes | None = None
) -> dict:
    """Low-level Gmail API call. Returns parsed JSON; raises RuntimeError with
    a short message on HTTP errors (never raw tracebacks to the agent)."""
    req = urllib.request.Request(url, data=body, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode(errors="replace")[:400]
        except Exception:
            pass
        raise RuntimeError(f"Gmail API HTTP {exc.code}: {detail[:200]}") from exc


def _bearer_token(acc: dict) -> str | None:
    from swarm_os.services.oauth2_loopback import get_valid_token

    return get_valid_token(acc.get("name") or "default", acc)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _fetch(token: str, msg_id: str, fmt: str) -> dict | None:
    url = f"{_GMAIL_BASE}/messages/{urllib.parse.quote(msg_id)}?format={fmt}"
    return _http_request("GET", url, _headers(token))


def _decode_b64(data: str | None) -> str:
    if not data:
        return ""
    pad = "=" * ((4 - len(data) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _msg_from_gmail(m: dict, with_body: bool) -> dict:
    """Mirror email_service._parse_msg's output shape from a Gmail API message."""
    payload = m.get("payload", {}) or {}
    headers = {
        h.get("name", ""): h.get("value", "") for h in payload.get("headers", [])
    }
    attachments = 0
    body_parts: list[str] = []

    def walk(p):
        nonlocal attachments
        filename = p.get("filename") or ""
        if filename:
            attachments += 1
        if with_body and p.get("mimeType") == "text/plain":
            if not filename:
                text = _decode_b64((p.get("body") or {}).get("data"))
                if text:
                    body_parts.append(text)
        for part in p.get("parts", []):
            walk(part)

    walk(payload)
    return {
        "id": m.get("id", ""),
        "subject": headers.get("Subject", ""),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "date": headers.get("Date", ""),
        "attachments": attachments,
        "body": "\n".join(body_parts)[:8000] if with_body else "",
        "message_id": headers.get("Message-ID", ""),
        "in_reply_to": headers.get("In-Reply-To", ""),
        "references": headers.get("References", ""),
        "list_unsubscribe": headers.get("List-Unsubscribe", ""),
    }


def gmail_list(
    acc: dict, folder: str = "INBOX", limit: int = 20, unread_only: bool = False
) -> dict:
    """List recent messages (metadata: subject/from/to/date, no body)."""
    token = _bearer_token(acc)
    if not token:
        return {
            "ok": False,
            "error": "Gmail API auth failed (no OAuth token; run the browser consent flow)",
        }
    try:
        params = {"maxResults": max(1, min(int(limit), _MAX_PAGE))}
        if unread_only:
            params["q"] = "is:unread"
        url = f"{_GMAIL_BASE}/messages?{urllib.parse.urlencode(params)}"
        data = _http_request("GET", url, _headers(token))
        ids = [x["id"] for x in data.get("messages", [])][: max(1, int(limit))]
        out = []
        for msg_id in ids:
            m = _fetch(token, msg_id, "metadata")
            if not m:
                continue
            p = _msg_from_gmail(m, with_body=False)
            p["unread"] = "UNREAD" in m.get("labelIds", [])
            p["ok"] = True
            out.append(p)
        return {"ok": True, "folder": folder, "count": len(out), "messages": out}
    except Exception as exc:
        log.warning("gmail_list failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def gmail_read(acc: dict, uid: str, folder: str = "INBOX") -> dict:
    """Read one message's full body."""
    token = _bearer_token(acc)
    if not token:
        return {"ok": False, "error": "Gmail API auth failed (no OAuth token)"}
    try:
        m = _fetch(token, uid, "full")
        if not m:
            return {"ok": False, "error": f"message {uid} not found"}
        p = _msg_from_gmail(m, with_body=True)
        p["ok"] = True
        return p
    except Exception as exc:
        log.warning("gmail_read failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def gmail_search(acc: dict, query: str, folder: str = "INBOX", limit: int = 20) -> dict:
    """Search via Gmail query syntax (e.g. 'from:x subject:y')."""
    token = _bearer_token(acc)
    if not token:
        return {"ok": False, "error": "Gmail API auth failed (no OAuth token)"}
    try:
        params = {"maxResults": max(1, min(int(limit), _MAX_PAGE)), "q": query}
        url = f"{_GMAIL_BASE}/messages?{urllib.parse.urlencode(params)}"
        data = _http_request("GET", url, _headers(token))
        ids = [x["id"] for x in data.get("messages", [])][: max(1, int(limit))]
        out = []
        for msg_id in ids:
            m = _fetch(token, msg_id, "metadata")
            if not m:
                continue
            p = _msg_from_gmail(m, with_body=False)
            p["unread"] = "UNREAD" in m.get("labelIds", [])
            p["ok"] = True
            out.append(p)
        return {"ok": True, "query": query, "count": len(out), "messages": out}
    except Exception as exc:
        log.warning("gmail_search failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def gmail_send_mime(acc: dict, mime_bytes: bytes) -> dict:
    """Send a fully-formed RFC822 MIME message via the Gmail API (raw send).

    Called by email_service._email_send after the human-approval gate has
    confirmed the send_token — never dispatches directly."""
    token = _bearer_token(acc)
    if not token:
        return {"ok": False, "error": "Gmail API auth failed (no OAuth token)"}
    raw = base64.urlsafe_b64encode(mime_bytes).decode()
    body = json.dumps({"raw": raw}).encode()
    try:
        data = _http_request(
            "POST",
            f"{_GMAIL_BASE}/messages/send",
            {**_headers(token), "Content-Type": "application/json"},
            body=body,
        )
        return {"ok": True, "id": data.get("id"), "thread_id": data.get("threadId")}
    except Exception as exc:
        log.warning("gmail_send_mime failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def gmail_modify_labels(
    acc: dict,
    uid: str,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> dict:
    """Apply label changes to a message (mark read/unread, archive, trash, move).

    add_labels/remove_labels use Gmail label names (UNREAD, INBOX, TRASH, or a
    user label). Called by email_service.email_manage — the mutation is gated
    upstream by the permission model."""
    token = _bearer_token(acc)
    if not token:
        return {"ok": False, "error": "Gmail API auth failed (no OAuth token)"}
    body = json.dumps(
        {
            "addLabelIds": add_labels or [],
            "removeLabelIds": remove_labels or [],
        }
    ).encode()
    try:
        _http_request(
            "POST",
            f"{_GMAIL_BASE}/messages/{urllib.parse.quote(uid)}/modify",
            {**_headers(token), "Content-Type": "application/json"},
            body=body,
        )
        return {
            "ok": True,
            "uid": uid,
            "add": add_labels or [],
            "remove": remove_labels or [],
        }
    except Exception as exc:
        log.warning("gmail_modify_labels failed: %s", exc)
        return {"ok": False, "error": str(exc)}
