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
import re
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


def _uses_gmail_api(acc: dict) -> bool:
    """True when the account opts into the Gmail REST API transport
    (HTTPS:443, OAuth token) instead of SMTP/IMAP. IMAP/SMTP stays the default."""
    return str(acc.get("transport", "")).lower() == "gmail_api"


def _uses_gmail_browser(acc: dict) -> bool:
    """True when the account opts into the browser-profile transport
    (HTTPS:443 only, persistent Playwright profile session) instead of SMTP/IMAP."""
    return str(acc.get("transport", "")).lower() == "gmail_browser"


def _gmail_transport(acc: dict):
    """Lazy import of the Gmail API transport (avoids a hard import cycle)."""
    from swarm_os.services import gmail_api

    return gmail_api


def _gmail_browser_transport(acc: dict):
    """Lazy import of the Gmail browser-profile transport."""
    from swarm_os.services import gmail_browser

    return gmail_browser


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
    imap_pass = (
        acc.get("app_password") or acc.get("password") or acc.get("access_token")
    )
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
                    body_parts.append(
                        part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace"
                        )
                    )
                except Exception:
                    pass
    else:
        try:
            body_parts.append(
                msg.get_payload(decode=True).decode(
                    msg.get_content_charset() or "utf-8", errors="replace"
                )
            )
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
        "message_id": _decode_header_value(msg.get("Message-ID", "")),
        "in_reply_to": _decode_header_value(msg.get("In-Reply-To", "")),
        "references": _decode_header_value(msg.get("References", "")),
        "list_unsubscribe": _decode_header_value(msg.get("List-Unsubscribe", "")),
    }


def email_list(
    folder: str = "INBOX",
    limit: int = 20,
    unread_only: bool = False,
    account: str | None = None,
) -> dict:
    """List recent messages in a folder (preview: subject/from/date/attachments)."""
    try:
        acc = _account(account)
        if not acc:
            return {
                "ok": False,
                "error": "email not configured (config/email_config.json missing)",
            }
        if _uses_gmail_api(acc):
            return _gmail_transport(acc).gmail_list(
                acc, folder=folder, limit=limit, unread_only=unread_only
            )
        if _uses_gmail_browser(acc):
            return _gmail_browser_transport(acc).gmail_browser_list(
                acc, folder=folder or "INBOX", limit=limit, unread_only=unread_only
            )
        conn = _get_imap(acc)
        try:
            conn.select(folder)
            typ, data = conn.uid("SEARCH", None, "UNSEEN" if unread_only else "ALL")
            if typ != "OK":
                return {"ok": False, "error": f"IMAP search failed: {typ}"}
            ids = data[0].split()
            ids = ids[-limit:] if ids else []
            out = []
            for i in ids:
                typ, msg_data = conn.uid("FETCH", i, "(RFC822)")
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
        if _uses_gmail_api(acc):
            return _gmail_transport(acc).gmail_read(acc, uid=uid, folder=folder)
        if _uses_gmail_browser(acc):
            return _gmail_browser_transport(acc).gmail_browser_read(
                acc, uid=uid, folder=folder or "INBOX"
            )
        conn = _get_imap(acc)
        try:
            conn.select(folder)
            typ, msg_data = conn.uid("FETCH", uid, "(RFC822)")
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


def email_search(
    query: str, folder: str = "INBOX", limit: int = 20, account: str | None = None
) -> dict:
    """Search by subject/from body substring (IMAP TEXT search)."""
    try:
        acc = _account(account)
        if not acc:
            return {"ok": False, "error": "email not configured"}
        if _uses_gmail_api(acc):
            return _gmail_transport(acc).gmail_search(
                acc, query=query, folder=folder, limit=limit
            )
        if _uses_gmail_browser(acc):
            return _gmail_browser_transport(acc).gmail_browser_search(
                acc, query=query, folder=folder or "INBOX", limit=limit
            )
        conn = _get_imap(acc)
        try:
            conn.select(folder)
            # IMAP TEXT search over a multi-word query: AND the terms.
            terms = query.split()
            typ, data = conn.uid("SEARCH", None, "ALL")
            if typ != "OK":
                return {"ok": False, "error": "search failed"}
            ids = data[0].split()
            # Fetch headers to filter locally (TEXT search is server-side but the
            # charset handling for non-ASCII is unreliable; header scan is robust).
            out = []
            for i in ids[-200:]:
                typ, msg_data = conn.uid("FETCH", i, "(BODY.PEEK[HEADER])")
                if typ != "OK" or not msg_data[0]:
                    continue
                header_blob = msg_data[0][1]
                head = email.message_from_bytes(header_blob)
                hay = " ".join(
                    [
                        _decode_header_value(head.get("Subject", "")),
                        _decode_header_value(head.get("From", "")),
                        _decode_header_value(head.get("To", "")),
                    ]
                ).lower()
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


def _build_message(
    to: str, subject: str, body: str, cc: str = "", attachments: list[str] | None = None
) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for path_str in attachments or []:
        p = Path(path_str)
        if p.exists() and p.is_file():
            part = MIMEApplication(p.read_bytes())
            part.add_header("Content-Disposition", "attachment", filename=p.name)
            msg.attach(part)
    return msg


def email_draft(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    attachments: list[str] | None = None,
    account: str | None = None,
) -> dict:
    """Stage a message as a draft and mint a short-lived send token. NOT sent yet —
    the caller must route email_send through the approval gate."""
    if not to or not subject:
        return {"ok": False, "error": "to and subject are required"}
    token = base64.urlsafe_b64encode(os.urandom(16)).decode()
    with _SEND_LOCK:
        _SEND_TOKENS[token] = {
            "to": to,
            "subject": subject,
            "body": body,
            "cc": cc,
            "attachments": attachments or [],
            "account": account,
            "created": time.time(),
        }
    return {
        "ok": True,
        "draft": {
            "to": to,
            "subject": subject,
            "body_len": len(body),
            "attachments": len(attachments or []),
        },
        "send_token": token,
        "expires_in_s": _TOKEN_TTL_S,
    }


def email_send(send_token: str, confirmed: bool = False) -> dict:
    """Send a staged draft, ONLY with an approval-confirmed token.

    The approval gate (tool_executor / Command Center) holds the token and only
    calls this with confirmed=True after a human approves. A token used without
    confirmed=True, or expired, is refused."""
    if not confirmed:
        return {
            "ok": False,
            "error": "email_send requires approval: the draft was returned with a send_token; confirm before sending",
        }
    with _SEND_LOCK:
        draft = _SEND_TOKENS.get(send_token)
        if not draft:
            return {
                "ok": False,
                "error": "unknown or expired send token (drafts expire after %ss)"
                % _TOKEN_TTL_S,
            }
        if time.time() - draft["created"] > _TOKEN_TTL_S:
            _SEND_TOKENS.pop(send_token, None)
            return {"ok": False, "error": "send token expired"}
        # Consume the token so it can't be sent twice.
        _SEND_TOKENS.pop(send_token, None)
    try:
        acc = _account(draft["account"])
        if not acc:
            return {"ok": False, "error": "email not configured"}
        msg = _build_message(
            draft["to"],
            draft["subject"],
            draft["body"],
            draft["cc"],
            draft["attachments"],
        )
        if _uses_gmail_api(acc):
            return _gmail_transport(acc).gmail_send_mime(acc, msg.as_bytes())
        if _uses_gmail_browser(acc):
            return _gmail_browser_transport(acc).gmail_browser_send(
                acc, draft["to"], draft["subject"], draft["body"]
            )
        smtp_host = acc.get("smtp_host") or acc.get("host")
        smtp_port = int(acc.get("smtp_port", 587))
        smtp_user = acc.get("user") or acc.get("email")
        smtp_pass = (
            acc.get("app_password") or acc.get("password") or acc.get("access_token")
        )
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
        return {
            "configured": False,
            "reason": "config/email_config.json missing or has no accounts",
        }
    if _uses_gmail_browser(acc):
        return {
            "configured": True,
            "account": acc.get("name") or acc.get("email"),
            "provider_hint": acc.get("provider", "gmail_browser"),
            "transport": "gmail_browser",
        }
    if _uses_gmail_api(acc):
        return {
            "configured": True,
            "account": acc.get("name") or acc.get("email"),
            "provider_hint": acc.get("provider", "gmail"),
            "transport": "gmail_api",
        }
    transport = "imap"
    return {
        "configured": True,
        "account": acc.get("name") or acc.get("email"),
        "provider_hint": acc.get("provider", "imap"),
        "transport": transport,
    }


# ---------------------------------------------------------------------------
# Inbox management (2026 SOTA — Spark/Perplexity Email parity)
# ---------------------------------------------------------------------------
_THREAD_SUBJECT_RE = re.compile(r"^\s*(?:re|fwd|fw|reply|aw|wg|sv)\s*:\s*", re.I)


def _thread_key(msg: dict) -> str:
    """Group key for a message: normalized subject + sender domain. This is the
    practical cross-transport thread identifier (IMAP has no reliable THREAD
    extension across providers, and Gmail's threadId is transport-specific)."""
    subject = _THREAD_SUBJECT_RE.sub("", msg.get("subject", "")).strip().lower()
    from_addr = msg.get("from", "")
    domain = ""
    m = re.search(r"@([\w.-]+)", from_addr or "")
    if m:
        domain = m.group(1).lower()
    return f"{domain}|{subject}"


def email_thread(
    uid: str, folder: str = "INBOX", account: str | None = None, limit: int = 200
) -> dict:
    """Return the messages that belong to the same thread as `uid` (grouped by
    normalized subject + sender domain, ordered by date)."""
    listing = email_list(folder, limit=limit, account=account)
    if not listing.get("ok"):
        return listing
    messages = listing.get("messages", [])
    target = next((m for m in messages if m.get("id") == uid), None)
    if not target:
        return {"ok": False, "error": f"message {uid} not found in {folder}"}
    key = _thread_key(target)
    members = [m for m in messages if _thread_key(m) == key]
    members.sort(key=lambda m: m.get("date", ""))
    return {
        "ok": True,
        "uid": uid,
        "thread_id": key,
        "count": len(members),
        "messages": members,
    }


def _parse_unsubscribe(raw: str) -> list[dict]:
    """Parse a List-Unsubscribe header into [{type, target}] mechanisms. The
    header carries comma-separated <mailto:...> and/or <https://...> entries."""
    mechanisms = []
    for piece in re.split(r",(?=\s*<)", raw or ""):
        piece = piece.strip().strip("<>").strip()
        if not piece:
            continue
        if piece.lower().startswith("mailto:"):
            mechanisms.append({"type": "mailto", "target": piece[len("mailto:") :]})
        elif piece.lower().startswith("http"):
            mechanisms.append({"type": "http", "target": piece})
    return mechanisms


def email_unsubscribe_scan(
    folder: str = "INBOX", limit: int = 50, account: str | None = None
) -> dict:
    """Scan recent mail for messages carrying a List-Unsubscribe header and
    report the unsubscribe mechanisms (newsletter-management parity)."""
    listing = email_list(folder, limit=limit, account=account)
    if not listing.get("ok"):
        return listing
    results = []
    for m in listing.get("messages", []):
        raw = m.get("list_unsubscribe", "")
        mech = _parse_unsubscribe(raw) if raw else []
        if mech:
            results.append(
                {
                    "uid": m.get("id"),
                    "subject": m.get("subject"),
                    "from": m.get("from"),
                    "date": m.get("date"),
                    "unsubscribe": mech,
                }
            )
    return {"ok": True, "folder": folder, "count": len(results), "messages": results}


def email_manage(
    op: str,
    uid: str,
    folder: str = "INBOX",
    target_folder: str | None = None,
    account: str | None = None,
) -> dict:
    """Inbox management ops: mark_read, mark_unread, archive (move to target
    folder / [Gmail]/All Mail), move (to target_folder), delete (trash)."""
    op = (op or "").lower()
    acc = _account(account)
    if not acc:
        return {"ok": False, "error": "email not configured"}
    if _uses_gmail_browser(acc):
        return {
            "ok": False,
            "error": "email_manage is not supported on the gmail_browser transport",
        }
    if _uses_gmail_api(acc):
        try:
            token = _gmail_transport(acc)._bearer_token(acc)
            if not token:
                return {"ok": False, "error": "Gmail API auth failed (no OAuth token)"}
            gmail = _gmail_transport(acc)
            if op == "mark_read":
                return gmail.gmail_modify_labels(acc, uid, remove_labels=["UNREAD"])
            if op == "mark_unread":
                return gmail.gmail_modify_labels(acc, uid, add_labels=["UNREAD"])
            if op == "archive":
                return gmail.gmail_modify_labels(acc, uid, remove_labels=["INBOX"])
            if op == "delete":
                return gmail.gmail_modify_labels(acc, uid, add_labels=["TRASH"])
            if op == "move" and target_folder:
                return gmail.gmail_modify_labels(
                    acc, uid, add_labels=[target_folder], remove_labels=["INBOX"]
                )
            return {"ok": False, "error": f"unknown email_manage op: {op}"}
        except Exception as exc:
            log.warning("email_manage (gmail_api) failed: %s", exc)
            return {"ok": False, "error": str(exc)}
    # IMAP transport
    try:
        conn = _get_imap(acc)
        try:
            conn.select(folder)
            if op == "mark_read":
                typ, _ = conn.uid("STORE", uid, "+FLAGS", r"(\Seen)")
                return {"ok": typ == "OK", "op": op, "uid": uid}
            if op == "mark_unread":
                typ, _ = conn.uid("STORE", uid, "-FLAGS", r"(\Seen)")
                return {"ok": typ == "OK", "op": op, "uid": uid}
            if op in ("archive", "move"):
                target = target_folder or "[Gmail]/All Mail"
                if op == "move" and not target_folder:
                    return {"ok": False, "error": "move requires target_folder"}
                typ, _ = conn.uid("COPY", uid, target)
                if typ != "OK":
                    return {"ok": False, "error": f"IMAP copy to {target} failed"}
                conn.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
                conn.expunge()
                return {"ok": True, "op": op, "uid": uid, "target": target}
            if op == "delete":
                typ, _ = conn.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
                if typ != "OK":
                    return {"ok": False, "error": "IMAP delete failed"}
                conn.expunge()
                return {"ok": True, "op": op, "uid": uid}
            return {"ok": False, "error": f"unknown email_manage op: {op}"}
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as exc:
        log.warning("email_manage failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# LLM-powered inbox features (Spark/Perplexity parity — synthesis over reads)
# ---------------------------------------------------------------------------
async def _acomplete(prompt: str, max_tokens: int = 800, timeout: float = 120.0) -> str:
    """Analysis-cloud completion (deepseek-v4-flash default), same contract as
    api_features.web_research / deep_research."""
    import os

    import litellm

    from ..core.settings import get_settings

    s = get_settings()
    model = getattr(s, "analysis_cloud_model", None) or "openai/deepseek-v4-flash"
    base = os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
    key = os.getenv("OPENAI_API_KEY", "")
    resp = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        api_base=base,
        api_key=key,
        custom_llm_provider="openai",
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return resp.choices[0].message.content or ""


async def email_summarize_thread(
    uid: str, folder: str = "INBOX", account: str | None = None
) -> dict:
    """Summarize a whole thread (all messages sharing the thread key) into a
    digestible briefing — Spark/Perplexity 'summarize this conversation'."""
    thread = email_thread(uid, folder=folder, account=account)
    if not thread.get("ok"):
        return thread
    members = thread.get("messages", [])
    if not members:
        return {"ok": False, "error": "empty thread"}
    body_block = []
    for m in members[:8]:
        subj = m.get("subject", "")
        frm = m.get("from", "")
        body = m.get("body", "")[:1500]
        body_block.append(f"[{frm}] {subj}\n{body}")
    prompt = (
        "You are a personal email assistant. Summarize the email thread below. "
        "Give: (1) a 2-3 sentence summary of the conversation, (2) what each "
        "participant wants / has agreed, (3) open action items with who owns them, "
        "(4) any deadlines or dates. Be precise and concise.\n\n"
        f"THREAD ({len(members)} messages):\n" + "\n\n---\n".join(body_block)
    )
    try:
        summary = await _acomplete(prompt, max_tokens=600)
    except Exception as exc:
        log.warning("email_summarize_thread LLM failed: %s", exc)
        summary = ""
    return {
        "ok": True,
        "uid": uid,
        "thread_id": thread.get("thread_id"),
        "message_count": len(members),
        "summary": summary
        or "summarization failed (LLM unavailable); see raw messages",
        "messages": [
            {k: m.get(k) for k in ("id", "from", "subject", "date")} for m in members
        ],
    }


async def email_reply_draft(
    uid: str, note: str = "", folder: str = "INBOX", account: str | None = None
) -> dict:
    """Draft a reply to a message, matching the sender's tone (read their message
    + thread, generate a natural reply). Returns the draft with a send_token —
    sending still requires the human approval gate (email_send)."""
    target = email_read(uid, folder=folder, account=account)
    if not target.get("ok"):
        return {"ok": False, "error": target.get("error", "message not found")}
    original_body = target.get("body", "")[:3000]
    thread = email_thread(uid, folder=folder, account=account)
    context_block = original_body
    if thread.get("ok") and len(thread.get("messages", [])) > 1:
        prev = thread["messages"][-2]["body"][:1000]
        context_block = (
            f"EARLIER IN THREAD:\n{prev}\n\nLATEST MESSAGE:\n{original_body}"
        )
    prompt = (
        "You are drafting an email reply for the user. Match the sender's tone "
        "(formal or casual) and keep it natural and concise. Use the note as the "
        "content instruction. Write ONLY the reply body — no subject, no greeting "
        "signature placeholders beyond a natural opening line.\n\n"
        f"MESSAGE TO REPLY TO (from: {target.get('from', '')}):\n{context_block}\n\n"
        f"YOUR NOTE: {note or 'write a polite, concise reply'}"
    )
    try:
        body = (await _acomplete(prompt, max_tokens=400)).strip()
    except Exception as exc:
        log.warning("email_reply_draft LLM failed: %s", exc)
        body = ""
    if not body:
        return {"ok": False, "error": "reply drafting failed (LLM unavailable)"}
    return email_draft(
        to=target.get("from", ""),
        subject=f"Re: {target.get('subject', '')}",
        body=body,
        account=account,
    )


async def email_digest(
    days: int = 7, folder: str = "INBOX", account: str | None = None
) -> dict:
    """Weekly/daily inbox digest: summarize what arrived in the window, group by
    theme (action items, newsletters, FYIs), and flag anything needing a reply —
    the Spark 'keep an eye on my inbox' capability, runnable on a schedule."""
    listing = email_list(folder, limit=100, account=account)
    if not listing.get("ok"):
        return listing
    messages = listing.get("messages", [])
    if not messages:
        return {
            "ok": True,
            "window_days": days,
            "count": 0,
            "digest": "No new mail in the window.",
        }
    block = []
    for m in messages[:40]:
        subj = (m.get("subject") or "")[:80]
        frm = (m.get("from") or "")[:60]
        unread = "UNREAD" if m.get("unread") else "read"
        body = (m.get("body") or "")[:300]
        block.append(f"[{unread}] from {frm} | {subj}\n{body}")
    prompt = (
        "You are a personal email assistant producing a digest. Summarize the "
        "recent emails below into: (1) ACTION ITEMS (emails that need a reply or "
        "a task), (2) NEWSLETTERS/PROMOS (fine to skim or unsubscribe), (3) FYI "
        "(read-when-free), (4) anything urgent. Be concise; use bullet lists.\n\n"
        f"EMAILS ({len(messages)} recent):\n" + "\n\n---\n".join(block)
    )
    try:
        digest = await _acomplete(prompt, max_tokens=800)
    except Exception as exc:
        log.warning("email_digest LLM failed: %s", exc)
        digest = ""
    return {
        "ok": True,
        "window_days": days,
        "count": len(messages),
        "digest": digest or "digest generation failed (LLM unavailable)",
        "degraded": not digest,
    }
