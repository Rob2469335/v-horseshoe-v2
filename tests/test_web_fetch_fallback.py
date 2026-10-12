"""Regression tests: web_fetch_handler uses the plain-HTTP engine only.

The optional Crawl4AI browser engine and its dependency chain (including
NLTK) were removed; these tests pin the surviving contract: the module
imports without Crawl4AI, the handler fetches via the pooled HTTP client
with per-hop SSRF checks, preserves the {ok,url,title,content} shape, and
fails closed on transport/HTTP errors.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import swarm_os.lib.mcp.web_search as ws

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HTML = (
    "<html><head><title> Example Page </title><script>evil()</script>"
    "<style>.x{}</style></head><body><h1>Hello</h1><p>World</p></body></html>"
)


class _FakeResponse:
    def __init__(self, status_code=200, text="", content_type="text/html"):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response or _FakeResponse(text=_HTML)
        self._exc = exc
        self.calls = []

    async def get(self, url, **kw):
        self.calls.append((url, kw))
        if self._exc is not None:
            raise self._exc
        return self._response


def _fetch(params, client):
    monkey_client = client
    orig = ws._get_client
    ws._get_client = lambda: monkey_client
    try:
        return asyncio.run(ws.web_fetch_handler(params))
    finally:
        ws._get_client = orig


def test_module_imports_without_crawl4ai():
    """Importing web_search must not require Crawl4AI: no import statement
    referencing it may remain in the module."""
    src = (_REPO_ROOT / "swarm_os" / "lib" / "mcp" / "web_search.py").read_text("utf-8")
    low = src.lower()
    assert "from crawl4ai" not in low
    assert "import crawl4ai" not in low


def test_web_fetch_uses_plain_http_and_preserves_contract():
    client = _FakeClient()
    result = _fetch({"url": "https://example.com/page"}, client)
    assert result["ok"] is True
    assert result["url"] == "https://example.com/page"
    assert result["title"] == "Example Page"
    assert "Hello" in result["content"] and "World" in result["content"]
    # script/style content is stripped, not returned
    assert "evil()" not in result["content"]
    assert client.calls and client.calls[0][0] == "https://example.com/page"
    assert client.calls[0][1].get("follow_redirects") is True


def test_web_fetch_truncates_to_max_chars():
    client = _FakeClient(response=_FakeResponse(text="<html><body>" + "A" * 5000 + "</body></html>"))
    result = _fetch({"url": "https://example.com/big", "max_chars": 100}, client)
    assert result["ok"] is True
    assert result["content"].endswith("...[FETCH TRUNCATED]...")
    assert len(result["content"]) < 200


def test_web_fetch_http_error_fails_closed():
    client = _FakeClient(response=_FakeResponse(status_code=503, text="nope"))
    result = _fetch({"url": "https://example.com/x"}, client)
    assert result["ok"] is False
    assert "HTTP 503" in result["error"]


def test_web_fetch_transport_exception_fails_closed():
    client = _FakeClient(exc=RuntimeError("boom"))
    result = _fetch({"url": "https://example.com/x"}, client)
    assert result["ok"] is False
    assert "boom" in result["error"]


def test_web_fetch_ssrf_still_enforced_before_any_request():
    client = _FakeClient()
    result = _fetch({"url": "http://127.0.0.1:6333/collections"}, client)
    assert result["ok"] is False
    assert "blocked" in result["error"]
    assert client.calls == []  # no request was made


def test_web_fetch_missing_url_fails_closed():
    result = _fetch({}, _FakeClient())
    assert result == {"ok": False, "error": "url is required"}
