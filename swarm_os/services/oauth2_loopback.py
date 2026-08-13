"""Fully-local OAuth2 loopback for email (2026 SOTA: no cloud relay).

Some providers (strictly-managed Gmail/Outlook tenants, iCloud) don't allow
app-password IMAP/SMTP. This module supports those via a local OAuth2 loopback:
- opens a local HTTP callback on 127.0.0.1:<port>
- launches the browser to the provider's authorize URL
- captures the `code` in the callback, exchanges it for a token
- refreshes the token before expiry (no manual re-auth)
- persists tokens to config/.email_tokens/ (gitignored)

The email_service uses the resulting access_token for XOAUTH2 IMAP/SMTP when the
account config has `"oauth2": true` and no app_password.

NOT wired into email_service yet for Gmail API sends (that needs the Gmail API,
not just SMTP) — this is the SMTP/IMAP XOAUTH2 path. Gmail API send is a
documented follow-up if needed.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

_TOKEN_DIR = Path("config/.email_tokens")


def _token_path(account_name: str) -> Path:
    return _TOKEN_DIR / f"{account_name}.json"


def _save_token(account_name: str, token: dict) -> None:
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    _token_path(account_name).write_text(json.dumps(token), encoding="utf-8")


def _load_token(account_name: str) -> dict | None:
    p = _token_path(account_name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_valid_token(account_name: str, cfg: dict) -> str | None:
    """Return a valid access token, refreshing if needed (or starting the flow
    if none exists). Returns None on failure (email stays app-password-only)."""
    token = _load_token(account_name)
    if not token:
        token = _run_flow(account_name, cfg)
        if not token:
            return None
        _save_token(account_name, token)
        return token.get("access_token")
    import time
    expires_at = token.get("expires_at", 0)
    if time.time() > expires_at - 60:
        refreshed = _refresh(account_name, cfg, token)
        if refreshed:
            _save_token(account_name, refreshed)
            return refreshed.get("access_token")
    return token.get("access_token")


def _run_flow(account_name: str, cfg: dict) -> dict | None:
    """OAuth2 authorization-code flow with a local loopback callback."""
    client_id = cfg.get("oauth2_client_id")
    auth_url_tpl = cfg.get("oauth2_auth_url")
    token_url = cfg.get("oauth2_token_url")
    scopes = cfg.get("oauth2_scopes", "https://mail.google.com/")
    redirect_port = int(cfg.get("oauth2_redirect_port", 3000))
    if not client_id or not auth_url_tpl or not token_url:
        log.warning("OAuth2 not fully configured for account %s", account_name)
        return None

    received: dict = {}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(q)
            received["code"] = params.get("code", [None])[0]
            received["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h3>Authorization received.</h3>"
                             b"<p>You can close this tab and return to the console.</p></body></html>")
        def log_message(self, *args):  # silence
            pass

    server = HTTPServer(("127.0.0.1", redirect_port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    state = urllib.parse.quote(account_name)
    authorize_url = (f"{auth_url_tpl}?client_id={urllib.parse.quote(client_id)}"
                     f"&redirect_uri={urllib.parse.quote(f'http://127.0.0.1:{redirect_port}/')}"
                     f"&response_type=code&scope={urllib.parse.quote(scopes)}&state={state}")
    if "accounts.google.com" in auth_url_tpl:
        # Google only returns a refresh_token with offline access; without it the
        # 1-hour access token expires and the user must re-consent every time.
        authorize_url += "&access_type=offline&prompt=consent"
    try:
        webbrowser.open(authorize_url)
    except Exception:
        log.warning("Could not auto-open browser; visit: %s", authorize_url)

    deadline = 180
    import time
    start = time.time()
    while time.time() - start < deadline and "code" not in received:
        time.sleep(0.5)
    server.shutdown()
    server.server_close()

    code = received.get("code")
    if not code:
        return None

    # Exchange code for token.
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": cfg.get("oauth2_client_secret", ""),
        "redirect_uri": f"http://127.0.0.1:{redirect_port}/",
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(token_url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            tok = json.loads(resp.read().decode())
    except Exception as exc:
        log.warning("OAuth2 token exchange failed: %s", exc)
        return None
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    return tok


def _refresh(account_name: str, cfg: dict, token: dict) -> dict | None:
    refresh_token = token.get("refresh_token")
    token_url = cfg.get("oauth2_token_url")
    if not refresh_token or not token_url:
        return None
    body = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": cfg.get("oauth2_client_id", ""),
        "client_secret": cfg.get("oauth2_client_secret", ""),
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(token_url, data=body)
    try:
        import time
        with urllib.request.urlopen(req, timeout=30) as resp:
            tok = json.loads(resp.read().decode())
        tok["refresh_token"] = refresh_token  # keep the original refresh token
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
        return tok
    except Exception as exc:
        log.warning("OAuth2 refresh failed for %s: %s", account_name, exc)
        return None
