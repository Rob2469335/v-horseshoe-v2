from __future__ import annotations
import ipaddress
import logging, os
from typing import Any, Dict
from urllib.parse import urlparse
import httpx

logger = logging.getLogger(__name__)

# SSRF guard: hosts that web_fetch must never read — the swarm's own loopback
# services (Qdrant 6333, llama.cpp 8080-8084, backend), private/link-local
# networks, and cloud-metadata endpoints.
def _ssrf_check(url: str) -> str | None:
    """Return a human-readable reason if url is an SSRF target, else None."""
    try:
        host = urlparse(url).hostname or ""
        host = host.strip("[]")
        # Cloud metadata / reserved link-local
        if host in ("169.254.169.254", "metadata.google.internal", "instance-data", "metadata"):
            return f"cloud-metadata host '{host}' is not allowed"
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return f"private/loopback/link-local address '{host}' is not allowed"
        except ValueError:
            pass  # hostname — resolve below
        import socket
        try:
            resolved = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return None  # unresolvable host — let the fetch fail naturally
        for family, _, _, _, sockaddr in resolved:
            try:
                ip = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                return f"host '{host}' resolves to non-public address {sockaddr[0]}"
    except Exception:
        pass
    return None

# UPGRADE: pooled client (avoids fresh TLS/connection per provider) + SSL verify
# enabled (was verify=False on every call — a security issue).
_client: httpx.AsyncClient | None = None


async def _ssrf_redirect_hook(response: httpx.Response):
    if response.is_redirect:
        loc = response.headers.get("location")
        if loc:
            from urllib.parse import urljoin
            next_url = urljoin(str(response.request.url), loc)
            blocked = _ssrf_check(next_url)
            if blocked:
                raise httpx.RequestError(f"SSRF blocked on redirect: {blocked}", request=response.request)

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=10.0),
            event_hooks={"response": [_ssrf_redirect_hook]}
        )
    return _client


async def web_search_handler(params: Dict[str, Any], trace_hook=None) -> Dict[str, Any]:
    query = params.get("query", "")
    max_results = int(params.get("max_results", 5))
    if not query:
        return {"ok": False, "error": "Search query is required"}

    tavily_key  = os.getenv("TAVILY_API_KEY", "").strip()
    serper_key  = os.getenv("SERPER_API_KEY", "").strip()
    brave_key   = os.getenv("BRAVE_API_KEY", "").strip()
    exa_key     = os.getenv("EXA_API_KEY", "").strip()
    serpapi_key = os.getenv("SERPAPI_KEY", "").strip()

    # Strip placeholder values
    for k in [tavily_key, serper_key, brave_key, exa_key, serpapi_key]:
        if k.startswith("your_") or k == "":
            k = ""

    tavily_key  = "" if tavily_key.startswith("your_")  else tavily_key
    serper_key  = "" if serper_key.startswith("your_")  else serper_key
    brave_key   = "" if brave_key.startswith("your_")   else brave_key
    exa_key     = "" if exa_key.startswith("your_")     else exa_key
    serpapi_key = "" if serpapi_key.startswith("your_") else serpapi_key

    client = _get_client()

    try:
        if tavily_key:
            r = await client.post("https://api.tavily.com/search",
                json={"api_key": tavily_key, "query": query, "max_results": max_results})
            if r.status_code < 400:
                items = r.json().get("results", [])[:max_results]
                return {"ok": True, "provider": "tavily", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("url",""),
                                     "snippet": i.get("content", i.get("snippet",""))} for i in items]}
            logger.warning("Tavily %s, trying next", r.status_code)

        if serper_key:
            r = await client.post("https://google.serper.dev/search",
                headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                json={"q": query, "num": max_results})
            if r.status_code < 400:
                items = r.json().get("organic", [])[:max_results]
                return {"ok": True, "provider": "serper", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("link", i.get("url","")),
                                     "snippet": i.get("snippet", i.get("content",""))} for i in items]}
            logger.warning("Serper %s, trying next", r.status_code)

        if brave_key:
            r = await client.get("https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
                params={"q": query, "count": max_results})
            if r.status_code < 400:
                items = r.json().get("web", {}).get("results", [])[:max_results]
                return {"ok": True, "provider": "brave", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("url",""),
                                     "snippet": i.get("description","")} for i in items]}
            logger.warning("Brave %s, trying next", r.status_code)

        if exa_key:
            r = await client.post("https://api.exa.ai/search",
                headers={"x-api-key": exa_key, "Content-Type": "application/json"},
                json={"query": query, "numResults": max_results})
            if r.status_code < 400:
                items = r.json().get("results", [])[:max_results]
                return {"ok": True, "provider": "exa", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("url",""),
                                     "snippet": i.get("text","")[:300]} for i in items]}
            logger.warning("Exa %s, trying next", r.status_code)

        if serpapi_key:
            r = await client.get("https://serpapi.com/search",
                params={"q": query, "api_key": serpapi_key, "num": max_results, "engine": "google"})
            if r.status_code < 400:
                items = r.json().get("organic_results", [])[:max_results]
                return {"ok": True, "provider": "serpapi", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("link",""),
                                     "snippet": i.get("snippet","")} for i in items]}
            logger.warning("SerpAPI %s, trying next", r.status_code)

        try:
            from duckduckgo_search import DDGS
            import asyncio
            def run_ddg():
                with DDGS() as ddgs:
                    return list(ddgs.text(query, max_results=max_results))
            results = await asyncio.to_thread(run_ddg)
            if results:
                return {"ok": True, "provider": "duckduckgo", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("href",""),
                                     "snippet": i.get("body","")} for i in results]}
        except Exception as ddg_err:
            logger.warning(f"DuckDuckGo search failed: {ddg_err}")

        logger.warning("All search providers failed or unconfigured")
        return {"ok": False, "error": "All configured search providers failed or are unconfigured. Please set an API key or ensure internet connectivity."}

    except Exception as e:
        logger.exception("Web search error")
        return {"ok": False, "error": str(e)}


async def web_fetch_handler(params: Dict[str, Any], trace_hook=None) -> Dict[str, Any]:
    """Fetch a URL and return clean markdown via Crawl4AI (LLM-optimized extraction).
    Falls back to plain HTTP+regex stripping if Crawl4AI is unavailable."""
    import re as _re

    url = str(params.get("url", "")).strip()
    if not url:
        return {"ok": False, "error": "url is required"}
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url

    # SECURITY: block SSRF targets — the swarm's own loopback services (Qdrant on
    # 6333, llama.cpp on 8080-8084, backend), private/link-local networks, and
    # cloud metadata endpoints. A malicious page/instruction must not make the
    # agent read internal state through web_fetch.
    _ssrf = _ssrf_check(url)
    if _ssrf:
        return {"ok": False, "error": f"web_fetch blocked: {_ssrf}"}

    max_chars = int(params.get("max_chars", 20000))

    # Crawl4AI: browser-level extraction → clean LLM-friendly markdown
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
        run_cfg = CrawlerRunConfig(
            page_timeout=15000,    # ms — gives JS-heavy pages time to render
            wait_until="domcontentloaded",
            remove_overlay_elements=True,   # dismiss cookie/GDPR popups
            word_count_threshold=10,        # discard near-empty extractions
        )
        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
        if result and result.markdown and len(result.markdown.strip()) > 50:
            text = result.markdown.strip()
            title = result.metadata.get("title", "") if result.metadata else ""
            if len(text) > max_chars:
                text = text[:max_chars] + "\n...[FETCH TRUNCATED]..."
            if trace_hook:
                trace_hook("web_fetch", {"ok": True, "url": url, "chars": len(text), "engine": "crawl4ai"})
            return {"ok": True, "url": url, "title": title or _extract_title_from_url(url), "content": text}
    except Exception as c4a_err:
        logger.debug("Crawl4AI fetch failed for %s, falling back to HTTP: %s", url, c4a_err)

    # Fallback: plain HTTP + regex HTML stripping
    client = _get_client()
    try:
        r = await client.get(
            url,
            follow_redirects=True,
            headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/124.0 Safari/537.36"),
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
            },
        )
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code} fetching {url}"}

        content_type = r.headers.get("content-type", "")
        text = r.text

        if "html" in content_type.lower():
            text = _re.sub(r"(?is)<script.*?</script>", " ", text)
            text = _re.sub(r"(?is)<style.*?</style>", " ", text)
            text = _re.sub(r"(?is)<[^>]+>", " ", text)
            text = _re.sub(r"\s+", " ", text).strip()

        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[FETCH TRUNCATED]..."

        if trace_hook:
            trace_hook("web_fetch", {"ok": True, "url": url, "chars": len(text)})
        return {"ok": True, "url": url, "title": _extract_title(r.text, content_type), "content": text}
    except Exception as e:
        logger.warning("Web fetch failed for %s: %s", url, e)
        return {"ok": False, "error": str(e)}


def _extract_title(html: str, content_type: str) -> str:
    import re as _re
    if "html" not in content_type.lower():
        return ""
    m = _re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return _re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _extract_title_from_url(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return host.replace("www.", "").replace(".com", "").replace(".org", "").replace(".io", "").replace(".net", "").title()
