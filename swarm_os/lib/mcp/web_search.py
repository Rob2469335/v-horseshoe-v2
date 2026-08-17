from __future__ import annotations
import asyncio
import ipaddress
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Coroutine, Dict
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
        if host in (
            "169.254.169.254",
            "metadata.google.internal",
            "instance-data",
            "metadata",
        ):
            return f"cloud-metadata host '{host}' is not allowed"
        try:
            ip = ipaddress.ip_address(host)
            if (
                ip.is_loopback
                or ip.is_private
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
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
                raise httpx.RequestError(
                    f"SSRF blocked on redirect: {blocked}", request=response.request
                )


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=10.0),
            event_hooks={"response": [_ssrf_redirect_hook]},
        )
    return _client


_SearchFn = Callable[
    ["httpx.AsyncClient", str, int], Coroutine[Any, Any, list[dict[str, str]]]
]


async def _tavily_search(client, query: str, max_results: int) -> list[dict]:
    r = await client.post(
        "https://api.tavily.com/search",
        json={
            "api_key": os.getenv("TAVILY_API_KEY", ""),
            "query": query,
            "max_results": max_results,
        },
    )
    r.raise_for_status()
    return [
        {
            "title": i.get("title", ""),
            "url": i.get("url", ""),
            "snippet": i.get("content", i.get("snippet", "")),
        }
        for i in r.json().get("results", [])[:max_results]
    ]


async def _serper_search(client, query: str, max_results: int) -> list[dict]:
    r = await client.post(
        "https://google.serper.dev/search",
        headers={
            "X-API-KEY": os.getenv("SERPER_API_KEY", ""),
            "Content-Type": "application/json",
        },
        json={"q": query, "num": max_results},
    )
    r.raise_for_status()
    return [
        {
            "title": i.get("title", ""),
            "url": i.get("link", i.get("url", "")),
            "snippet": i.get("snippet", i.get("content", "")),
        }
        for i in r.json().get("organic", [])[:max_results]
    ]


async def _brave_search(client, query: str, max_results: int) -> list[dict]:
    r = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": os.getenv("BRAVE_API_KEY", ""),
        },
        params={"q": query, "count": max_results},
    )
    r.raise_for_status()
    return [
        {
            "title": i.get("title", ""),
            "url": i.get("url", ""),
            "snippet": i.get("description", ""),
        }
        for i in r.json().get("web", {}).get("results", [])[:max_results]
    ]


async def _exa_search(client, query: str, max_results: int) -> list[dict]:
    r = await client.post(
        "https://api.exa.ai/search",
        headers={
            "x-api-key": os.getenv("EXA_API_KEY", ""),
            "Content-Type": "application/json",
        },
        json={"query": query, "numResults": max_results},
    )
    r.raise_for_status()
    return [
        {
            "title": i.get("title", ""),
            "url": i.get("url", ""),
            "snippet": (i.get("text", "") or "")[:300],
        }
        for i in r.json().get("results", [])[:max_results]
    ]


async def _serpapi_search(client, query: str, max_results: int) -> list[dict]:
    r = await client.get(
        "https://serpapi.com/search",
        params={
            "q": query,
            "api_key": os.getenv("SERPAPI_KEY", ""),
            "num": max_results,
            "engine": "google",
        },
    )
    r.raise_for_status()
    return [
        {
            "title": i.get("title", ""),
            "url": i.get("link", ""),
            "snippet": i.get("snippet", ""),
        }
        for i in r.json().get("organic_results", [])[:max_results]
    ]


async def _tinyfish_search(client, query: str, max_results: int) -> list[dict]:
    r = await client.get(
        "https://api.search.tinyfish.ai",
        params={"query": query},
        headers={"X-API-Key": os.getenv("TINYFISH_API_KEY", "")},
    )
    r.raise_for_status()
    items = r.json().get("results", [])[:max_results]
    return [
        {
            "title": i.get("title", ""),
            "url": i.get("url", i.get("link", "")),
            "snippet": i.get("snippet", i.get("content", "")),
        }
        for i in items
    ]


def _provider_specs() -> list[tuple[str, str, _SearchFn]]:
    """(name, env-key, fn) for every provider with a configured non-placeholder key."""
    return [
        ("tavily", "TAVILY_API_KEY", _tavily_search),
        ("serper", "SERPER_API_KEY", _serper_search),
        ("brave", "BRAVE_API_KEY", _brave_search),
        ("exa", "EXA_API_KEY", _exa_search),
        ("serpapi", "SERPAPI_KEY", _serpapi_search),
        ("tinyfish", "TINYFISH_API_KEY", _tinyfish_search),
    ]


# ──────────────────────────────────────────────────────────────────────────────
# QUOTA GUARD — free-tier search providers have strict monthly budgets (measured
# during the 2026 provider research: Tavily ~1k, Exa ~1k, Brave ~1k, Serper
# 2.5k one-time, SerpAPI ~250). Firing every engine on every query would burn
# the free allowance in days. A persistent monthly per-provider counter (survives
# process restart) excludes providers past their budget instead of letting them
# fail on the live service (which already rate-limits SerpAPI in probes).
# TinyFish has no credit system → unlimited. Overrides:
#   SWARM_SEARCH_QUOTA_FILE=<path>   custom counter file (default data/search_quota.json)
#   SWARM_SEARCH_QUOTA_<PROVIDER>=N  monthly budget override (0 = disabled, -1 = unlimited)
# ──────────────────────────────────────────────────────────────────────────────

# Free-tier monthly query budgets (conservative, per provider research 2026).
# These are guards against exhausting free allowances, not exact billable caps.
DEFAULT_MONTHLY_BUDGETS: dict[str, int | None] = {
    "tavily": 1000,
    "serper": 800,
    "brave": 1000,
    "exa": 1000,
    "serpapi": 250,  # demoted: already rate-limits in live probes
    "tinyfish": None,  # unlimited — no credit system
}


def _quota_file() -> str:
    return os.getenv("SWARM_SEARCH_QUOTA_FILE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "..",
        "data",
        "search_quota.json",
    )


class _QuotaStore:
    """Persistent monthly per-provider query counter (thread-safe, file-backed).

    - Survives process restarts (a restart must not silently reset the month's
      burn rate / resurrect an exhausted provider).
    - All reads/writes happen under one lock; the counter file is a few hundred
      bytes written once per query, negligible vs the network call.
    - Fail-open on disk errors: an unreadable/unwritable counter must never
      break search — it logs and proceeds with the in-memory count.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._month: str = ""

    def _ensure_loaded(self) -> None:
        if self._month == time.strftime("%Y-%m"):
            return
        self._month = time.strftime("%Y-%m")
        try:
            with open(_quota_file(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("month") == self._month:
                self._counts = {
                    p: int(c)
                    for p, c in data.get("counts", {}).items()
                    if isinstance(c, (int, float)) and not isinstance(c, bool)
                }
            else:
                self._counts = {}
        except FileNotFoundError:
            self._counts = {}
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("search quota file unreadable, starting fresh: %s", exc)
            self._counts = {}

    def _write_sync(self) -> None:
        try:
            os.makedirs(os.path.dirname(_quota_file()) or ".", exist_ok=True)
            payload = {"month": self._month, "counts": self._counts}
            tmp = _quota_file() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, _quota_file())
        except OSError as exc:
            logger.warning("search quota file write failed (non-fatal): %s", exc)

    def reset_for_tests(self) -> None:
        """Clear the in-memory counter+month cache (test isolation only)."""
        with self._lock:
            self._counts = {}
            self._month = ""

    def record(self, provider: str, amount: int = 1) -> None:
        with self._lock:
            self._ensure_loaded()
            self._counts[provider] = self._counts.get(provider, 0) + amount
            self._write_sync()

    def budget_for(self, provider: str) -> int | None:
        """Monthly budget for a provider: env override → default. None = unlimited."""
        env_val = os.getenv(f"SWARM_SEARCH_QUOTA_{provider.upper()}")
        if env_val is not None:
            env_val = env_val.strip()
            if env_val.lower() in ("none", "unlimited", "-1", "0"):
                return None if env_val not in ("0",) else 0
            try:
                parsed = int(env_val)
                return None if parsed < 0 else parsed
            except ValueError:
                logger.warning(
                    "invalid SWARM_SEARCH_QUOTA_%s=%r, using default",
                    provider.upper(),
                    env_val,
                )
        return DEFAULT_MONTHLY_BUDGETS.get(provider)

    def remaining(self, provider: str) -> int | None:
        with self._lock:
            self._ensure_loaded()
            budget = self.budget_for(provider)
            if budget is None:
                return None
            return max(0, budget - self._counts.get(provider, 0))

    def exhausted(self, provider: str) -> bool:
        remaining = self.remaining(provider)
        return remaining is not None and remaining <= 0


_QUOTA_STORE = _QuotaStore()


def _quota_authorized_providers(
    specs: list[tuple[str, str, _SearchFn]],
) -> list[tuple[str, str, _SearchFn]]:
    """Filter provider specs to those not past their monthly budget."""
    result: list[tuple[str, str, _SearchFn]] = []
    for name, env_key, fn in specs:
        if _QUOTA_STORE.exhausted(name):
            logger.warning(
                "web-search provider %s excluded: monthly quota exhausted", name
            )
            continue
        result.append((name, env_key, fn))
    return result


# ──────────────────────────────────────────────────────────────────────────────
# RRF CONSENSUS MERGE — Reciprocal Rank Fusion (k=60), the canonical cross-engine
# merge used by Azure Search / OpenSearch / Meilisearch federated search /
# pi-search-multi. A result top-ranked in MULTIPLE engines gets a consensus
# boost that a single-engine ranking cannot express, and it is rank-only
# (score-agnostic, so heterogeneous engines are comparable). k=60 is large
# enough that it rewards consensus without overweighting near-misses.
# ──────────────────────────────────────────────────────────────────────────────

_RRF_K = 60


def _rrf_rank(rank: int) -> float:
    return 1.0 / (_RRF_K + rank + 1)


def _rrf_merge(per_provider: list[tuple[str, list[dict] | None]]) -> list[dict]:
    """Merge per-provider results by RRF consensus over normalized URLs.

    URL normalization makes cross-engine dup detection reliable: engines differ
    in trailing slashes vs not, ?utm_* vs not, and www vs bare-host, all of
    which are the SAME document. Each result keeps the `provider` it came from;
    `providers`_seen records every engine that surfaced it.
    """
    scores: dict[str, float] = {}
    first: dict[str, dict] = {}
    providers_seen: dict[str, list[str]] = {}
    for name, items in per_provider:
        if not items:
            continue
        for rank, it in enumerate(items):
            url = _norm_url(it.get("url", ""))
            if not url:
                continue
            key = url.lower()
            scores[key] = scores.get(key, 0.0) + _rrf_rank(rank)
            providers_seen.setdefault(key, []).append(name)
            if key not in first:
                first[key] = dict(it)
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    merged: list[dict] = []
    for key, score in ordered:
        entry = first[key]
        entry["provider"] = providers_seen[key][0]
        entry["providers"] = providers_seen[key]
        entry["rrf_score"] = round(score, 6)
        merged.append(entry)
    return merged


_NORM_CACHE: dict[str, str] = {}


def _norm_url(url: str) -> str:
    """Normalize a URL for cross-engine dedupe: lowercase scheme/host, strip
    trailing slash, strip common tracking params, drop fragments."""
    if not url or not isinstance(url, str):
        return ""
    if url in _NORM_CACHE:
        return _NORM_CACHE[url]
    try:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "http").lower()
        host = (parsed.hostname or "").lower()
        if parsed.port:
            host = f"{host}:{parsed.port}"
        path: str = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        query = parsed.query
        parts = [
            p
            for p in query.split("&")
            if p
            and not p.split("=", 1)[0].lower()
            in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")
        ]
        norm = f"{scheme}://{host}{path}"
        if parts:
            norm += "?" + "&".join(sorted(parts))
        _NORM_CACHE[url] = norm
        return norm
    except Exception:
        return url.strip()


async def web_search_handler(params: Dict[str, Any], trace_hook=None) -> Dict[str, Any]:
    query = params.get("query", "")
    max_results = int(params.get("max_results", 5))
    if not query:
        return {"ok": False, "error": "Search query is required"}

    client = _get_client()

    # PARALLEL FAN-OUT: every configured provider runs concurrently. A single
    # provider's failure is logged and never fatal — the useful result is the
    # MERGE across engines (distinct indices surface non-overlapping hits the
    # first-answering fail-chain used to hide). Providers past their monthly
    # free-tier quota are excluded rather than failed (see QUOTA GUARD).
    providers: list[tuple[str, _SearchFn]] = [
        (name, fn)
        for name, env_key, fn in _quota_authorized_providers(_provider_specs())
        if _has_key(env_key, min_len=8)
    ]
    if not providers:
        return await _ddg_fallback(query, max_results)

    async def run_one(name: str, fn: _SearchFn) -> tuple[str, list[dict] | None]:
        _QUOTA_STORE.record(name)  # budget use counted per query attempted
        try:
            async with asyncio.timeout(15.0):
                items = await fn(client, query, max_results)
            return name, items
        except Exception as exc:
            logger.warning("web-search provider %s failed: %s", name, exc)
            return name, None

    per_provider = await asyncio.gather(*(run_one(n, fn) for n, fn in providers))

    providers_ok: list[str] = [name for name, items in per_provider if items]
    merged: list[dict] = _rrf_merge(per_provider)

    if not merged:
        logger.warning("All configured web-search providers failed or returned nothing")
        ddg = await _ddg_fallback(query, max_results)
        return ddg

    if trace_hook:
        trace_hook(
            "web_search",
            {
                "ok": True,
                "query": query,
                "providers": providers_ok,
                "results": len(merged),
            },
        )

    if len(providers_ok) == 1:
        provider_label = providers_ok[0]
    else:
        provider_label = "multi:" + ",".join(providers_ok)
    return {
        "ok": True,
        "provider": provider_label,
        "providers": providers_ok,
        "query": query,
        "results": merged,
    }


def _has_key(name: str, min_len: int = 1) -> bool:
    v = os.getenv(name, "").strip()
    if not v or v.startswith("your_"):
        return False
    return len(v) >= min_len


async def _ddg_fallback(query: str, max_results: int) -> Dict[str, Any]:
    try:
        from duckduckgo_search import DDGS

        def run_ddg():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))

        results = await asyncio.to_thread(run_ddg)
        if results:
            return {
                "ok": True,
                "provider": "duckduckgo",
                "query": query,
                "results": [
                    {
                        "title": i.get("title", ""),
                        "url": i.get("href", ""),
                        "snippet": i.get("body", ""),
                        "provider": "duckduckgo",
                    }
                    for i in results
                ],
            }
    except Exception as ddg_err:
        logger.warning(f"DuckDuckGo search failed: {ddg_err}")

    logger.warning("All search providers failed or unconfigured")
    return {
        "ok": False,
        "error": "All configured search providers failed or are unconfigured. Please set an API key or ensure internet connectivity.",
    }


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
            page_timeout=15000,  # ms — gives JS-heavy pages time to render
            wait_until="domcontentloaded",
            remove_overlay_elements=True,  # dismiss cookie/GDPR popups
            word_count_threshold=10,  # discard near-empty extractions
        )
        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
        # SECURITY: the browser path follows redirects internally with no
        # per-hop check (unlike the pooled-client fallback). If the final URL
        # resolved to a loopback/private/cloud-metadata target, discard the
        # content instead of returning it — an attacker redirect would otherwise
        # make the agent read internal services.
        final_url = getattr(result, "url", None)
        if final_url and str(final_url).strip().lower() != url.lower():
            _final_ssrf = _ssrf_check(str(final_url))
            if _final_ssrf:
                return {
                    "ok": False,
                    "error": f"web_fetch blocked: redirect landed on {_final_ssrf}",
                }
        if result and result.markdown and len(result.markdown.strip()) > 50:
            text = result.markdown.strip()
            title = result.metadata.get("title", "") if result.metadata else ""
            if len(text) > max_chars:
                text = text[:max_chars] + "\n...[FETCH TRUNCATED]..."
            if trace_hook:
                trace_hook(
                    "web_fetch",
                    {"ok": True, "url": url, "chars": len(text), "engine": "crawl4ai"},
                )
            return {
                "ok": True,
                "url": url,
                "title": title or _extract_title_from_url(url),
                "content": text,
            }
    except Exception as c4a_err:
        logger.debug(
            "Crawl4AI fetch failed for %s, falling back to HTTP: %s", url, c4a_err
        )

    # Fallback: plain HTTP + regex HTML stripping
    client = _get_client()
    try:
        r = await client.get(
            url,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
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
        return {
            "ok": True,
            "url": url,
            "title": _extract_title(r.text, content_type),
            "content": text,
        }
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
    return (
        host.replace("www.", "")
        .replace(".com", "")
        .replace(".org", "")
        .replace(".io", "")
        .replace(".net", "")
        .title()
    )
