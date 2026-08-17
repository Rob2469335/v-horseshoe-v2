"""Gmail over the app's persistent Playwright browser profile (HTTPS:443 only).

transport="gmail_browser". Drives the SAME persistent Chromium profile used by
the browser tool (`data/browser_profile`) through the Gmail web UI over https.
This avoids SMTP/IMAP ports entirely — which on this machine are MITM'd by the
Avast mail shield (raw issuer "CN=Avast Web/Mail Shield Root"). The profile
holds a signed-in Google session (cookies persist on disk), so NO OAuth
client id/secret and no app password are needed.

Design (matches email_service conventions):
- Read ops (list/read/search) hit Gmail search/thread URLs and parse the DOM.
- Send composes in the Gmail Compose UI. The approval gate in email_service
  still applies BEFORE this module is ever reached (email_send only calls us
  after a confirmed send_token).
- Attachments are NOT supported on the browser send path (a human composing in
  a real browser is the right tool for attachments).
- Every public function never raises: returns {"ok": False, "error": "..."}.
- Runs the browser work on its own temp loop when the caller already has a
  running loop (email_service sync facades are invoked from threadpool threads,
  which have none; agent paths may have one).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_PROFILE_DIR = Path(os.getenv("ZENITH_BROWSER_PROFILE", "data/browser_profile"))
_CHANNEL = "chrome"
_NAV_TIMEOUT_MS = 45000

# Extract the message rows from the Gmail list view (tr.zA), with each row's
# thread id (needed as the `uid` for gmail_browser_read). This is the exact
# shape we verified live against the real mailbox.
_ROW_JS = """() => {
  const rows = [];
  document.querySelectorAll('tr.zA').forEach(tr => {
    const txt = (s) => { const e = tr.querySelector(s); return e ? e.textContent.trim() : ''; };
    const idEl = tr.querySelector('.bqe[data-thread-id]') || tr.querySelector('[data-thread-id]');
    const tid = idEl ? (idEl.getAttribute('data-thread-id') || '') : '';
    const m = tid.match(/#thread-f:(.+)/);
    const id = m ? m[1] : tid.replace(/^#thread-f:/, '');
    const fromEl = tr.querySelector('td.yX span[email]');
    const from = fromEl ? (fromEl.getAttribute('email') || fromEl.textContent.trim()) : '';
    const subj = txt('td.xY .bog span.bog') || txt('td.xY .bog');
    const snip = txt('td.xY .y2') || txt('td.xY .y6');
    const timeEl = tr.querySelector('td.xW span[title]');
    const time = timeEl ? (timeEl.getAttribute('title') || timeEl.textContent.trim()) : txt('td.xW');
    const unread = (tr.getAttribute('class') || '').includes('zE');
    if ((subj || from) && id) rows.push({ id, from, subject: subj, snippet: snip.slice(0, 220), time, unread });
  });
  return rows;
}"""

# Full-message body + subject from a thread view.
_BODY_JS = """() => {
  const subjEl = document.querySelector('h2.hP') || document.querySelector('h2[data-thread-perm-id]');
  const bodyEl = document.querySelector('.a3s.aiL') || document.querySelector('[dir="ltr"] .a3s');
  const subject = subjEl ? subjEl.textContent.trim() : '';
  const body = bodyEl ? bodyEl.innerText.trim() : '';
  const meta = document.querySelector('.g2 .gD, .gD') ;
  const frm = meta ? meta.getAttribute('email') || meta.textContent.trim() : '';
  return { subject, from: frm, body: body.slice(0, 24000) };
}"""


def _run_in_loop(coro):
    """Run a coroutine to completion. Prefers this thread's loop if it has one,
    else a fresh loop via asyncio.run; falls back to a temp loop in a daemon
    thread when a loop is already running here (async-embedded caller)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    loop = asyncio.new_event_loop()
    out = []
    errs = []

    def _go():
        try:
            out.append(loop.run_until_complete(coro))
        except BaseException as exc:  # noqa: BLE001 - re-raised in caller thread
            errs.append(exc)
        finally:
            loop.close()

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t.join()
    if errs:
        raise errs[0]
    return out[0]


async def _open_context(pw):
    """Launch the persistent profile through the real Chrome binary (hides the
    automation fingerprint Google's sign-in rejects)."""
    _profile = _PROFILE_DIR.mkdir(parents=True, exist_ok=True) or _PROFILE_DIR
    return await pw.chromium.launch_persistent_context(
        user_data_dir=str(_profile),
        channel=_CHANNEL,
        headless=False,
        viewport={"width": 1280, "height": 720},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )


async def _list_impl(folder: str, limit: int, unread_only: bool) -> dict:
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        ctx = await _open_context(pw)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(
                "https://mail.google.com/mail/u/0/inbox",
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT_MS,
            )
            html_ok = False
            for _ in range(6):
                await asyncio.sleep(2)
                try:
                    html_ok = bool(
                        await page.evaluate(
                            "() => document.querySelectorAll('tr.zA').length > 0"
                        )
                    )
                except Exception:
                    html_ok = False
                if html_ok:
                    break
                if "accounts.google.com" in page.url:
                    return {
                        "ok": False,
                        "error": "not signed into Google in the browser profile (%s)"
                        % _PROFILE_DIR,
                        "url": page.url,
                    }
            if unread_only:
                await page.goto(
                    "https://mail.google.com/mail/u/0/#search/is%3Aunread",
                    wait_until="domcontentloaded",
                    timeout=_NAV_TIMEOUT_MS,
                )
                for _ in range(4):
                    await asyncio.sleep(2)
                    try:
                        if bool(
                            await page.evaluate(
                                "() => document.querySelectorAll('tr.zA').length > 0"
                            )
                        ):
                            break
                    except Exception:
                        pass
            rows = await page.evaluate(_ROW_JS)
            if not rows and not html_ok:
                return {
                    "ok": False,
                    "error": "Gmail rows did not render",
                    "url": page.url,
                }
            return {
                "ok": True,
                "folder": folder,
                "count": len(rows[:limit]),
                "messages": rows[:limit],
                "url": page.url,
            }
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


_CLICK_THREAD_JS = """(tid) => {
  const row = [...document.querySelectorAll('tr.zA')].find(r => {
    const e = r.querySelector('[data-thread-id]');
    return e && e.getAttribute('data-thread-id') === '#thread-f:' + tid;
  });
  if (!row) return false;
  row.click();
  return true;
}"""


async def _read_impl(uid: str) -> dict:
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        ctx = await _open_context(pw)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(
                "https://mail.google.com/mail/u/0/inbox",
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT_MS,
            )
            for _ in range(6):
                await asyncio.sleep(2)
                try:
                    if await page.evaluate(
                        "() => document.querySelectorAll('tr.zA').length > 0"
                    ):
                        break
                except Exception:
                    pass
            if "accounts.google.com" in page.url:
                return {
                    "ok": False,
                    "error": "not signed into Google in the browser profile (%s)"
                    % _PROFILE_DIR,
                }
            clicked = await page.evaluate(_CLICK_THREAD_JS, uid)
            if not clicked:
                return {
                    "ok": False,
                    "error": "thread %s not found in mailbox view" % uid,
                }
            data = {}
            for _ in range(6):
                await asyncio.sleep(2)
                data = await page.evaluate(_BODY_JS)
                if data.get("subject") or data.get("body"):
                    break
            if not data.get("subject") and not data.get("body"):
                return {
                    "ok": False,
                    "error": "thread %s not found or message view did not render" % uid,
                }
            return {
                "ok": True,
                "id": uid,
                "subject": data["subject"],
                "from": data.get("from", ""),
                "to": "",
                "date": "",
                "attachments": 0,
                "body": data["body"][:8000],
            }
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


async def _search_impl(query: str, folder: str, limit: int) -> dict:
    from playwright.async_api import async_playwright
    import urllib.parse

    pw = await async_playwright().start()
    try:
        ctx = await _open_context(pw)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(
                "https://mail.google.com/mail/u/0/inbox",
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT_MS,
            )
            for _ in range(6):
                await asyncio.sleep(2)
                try:
                    if await page.evaluate(
                        "() => document.querySelectorAll('tr.zA').length > 0"
                    ):
                        break
                except Exception:
                    pass
            if "accounts.google.com" in page.url:
                return {
                    "ok": False,
                    "error": "not signed into Google in the browser profile (%s)"
                    % _PROFILE_DIR,
                }
            tag = (
                "in:%s " % folder
                if folder and folder.lower() not in ("", "all")
                else ""
            )
            q = urllib.parse.quote((tag + query).strip())
            await page.goto(
                "https://mail.google.com/mail/u/0/#search/%s" % q,
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT_MS,
            )
            for _ in range(4):
                await asyncio.sleep(2)
                try:
                    if bool(
                        await page.evaluate(
                            "() => document.querySelectorAll('tr.zA').length > 0"
                        )
                    ):
                        break
                except Exception:
                    pass
            rows = await page.evaluate(_ROW_JS)
            return {
                "ok": True,
                "query": query,
                "folder": folder,
                "count": len(rows[:limit]),
                "messages": rows[:limit],
            }
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


async def _send_impl(to: str, subject: str, body: str) -> dict:
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        ctx = await _open_context(pw)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(
                "https://mail.google.com/mail/u/0/#inbox",
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT_MS,
            )
            await asyncio.sleep(3)
            if "accounts.google.com" in page.url:
                return {
                    "ok": False,
                    "error": "not signed into Google in the browser profile (%s)"
                    % _PROFILE_DIR,
                }
            compose = None
            for sel in (
                'div[gh="cm"]',
                '[role="button"][aria-label*="ompose"]',
                'a[href="#inbox?compose=new"]',
            ):
                try:
                    loc = page.locator(sel).first
                    if await loc.count() and await loc.is_visible():
                        compose = loc
                        break
                except Exception:
                    continue
            if compose is None:
                return {
                    "ok": False,
                    "error": "Compose button not found in the Gmail UI",
                }
            await compose.click(timeout=8000)
            await asyncio.sleep(2)
            filled = False
            for sel in (
                'input[name="to"]',
                'input[aria-label*="ecipients"]',
                'div[aria-label*="o recipients"] input',
            ):
                try:
                    loc = page.locator(sel).first
                    if await loc.count():
                        await loc.fill(to, timeout=8000)
                        filled = True
                        break
                except Exception:
                    continue
            if not filled:
                return {
                    "ok": False,
                    "error": "To field not found in the compose window",
                }
            subj_ok = False
            for sel in (
                'input[name="subjectbox"]',
                'input[aria-label*="ubject"]',
                'input[name="subject"]',
            ):
                try:
                    loc = page.locator(sel).first
                    if await loc.count():
                        await loc.fill(subject, timeout=8000)
                        subj_ok = True
                        break
                except Exception:
                    continue
            if not subj_ok:
                return {
                    "ok": False,
                    "error": "Subject field not found in the compose window",
                }
            body_ok = False
            for sel in (
                'div[aria-label="Message Body"]',
                'div[aria-label*="essage body"]',
                ".Am.Al.editable",
            ):
                try:
                    loc = page.locator(sel).first
                    if await loc.count():
                        await loc.click(timeout=5000)
                        await page.keyboard.type(body[:8000], delay=0)
                        body_ok = True
                        break
                except Exception:
                    continue
            if not body_ok:
                return {
                    "ok": False,
                    "error": "Message body field not found in the compose window",
                }
            send = None
            for sel in (
                'div[data-tooltip*="Send"]',
                '[role="button"][aria-label*="Send"]',
                'div[gh="k"]',
            ):
                try:
                    loc = page.locator(sel).first
                    if await loc.count() and await loc.is_visible():
                        send = loc
                        break
                except Exception:
                    continue
            if send is None:
                return {
                    "ok": False,
                    "error": "Send button not found in the compose window",
                }
            await send.click(timeout=8000)
            await asyncio.sleep(3)
            sent = (
                "sent" in page.url
                or "Sent" in await page.title()
                or "Compose" not in await page.title()
            )
            return {
                "ok": sent,
                "to": to,
                "subject": subject,
                "error": None
                if sent
                else "send clicked but could not confirm the message was sent",
            }
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


def gmail_browser_list(
    acc: dict, folder: str = "INBOX", limit: int = 20, unread_only: bool = False
) -> dict:
    try:
        return _run_in_loop(
            _list_impl(folder=folder, limit=int(limit), unread_only=bool(unread_only))
        )
    except Exception as exc:
        log.warning("gmail_browser_list failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def gmail_browser_read(acc: dict, uid: str, folder: str = "INBOX") -> dict:
    try:
        return _run_in_loop(_read_impl(uid=uid))
    except Exception as exc:
        log.warning("gmail_browser_read failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def gmail_browser_search(
    acc: dict, query: str, folder: str = "INBOX", limit: int = 20
) -> dict:
    try:
        return _run_in_loop(_search_impl(query=query, folder=folder, limit=int(limit)))
    except Exception as exc:
        log.warning("gmail_browser_search failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def gmail_browser_send(acc: dict, to: str, subject: str, body: str) -> dict:
    try:
        return _run_in_loop(_send_impl(to=to, subject=subject, body=body))
    except Exception as exc:
        log.warning("gmail_browser_send failed: %s", exc)
        return {"ok": False, "error": str(exc)}
