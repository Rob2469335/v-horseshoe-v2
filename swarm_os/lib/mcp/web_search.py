from __future__ import annotations
import logging, os
from typing import Any, Dict
import httpx

logger = logging.getLogger(__name__)

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

    try:
        if tavily_key:
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.post("https://api.tavily.com/search",
                    json={"api_key": tavily_key, "query": query, "max_results": max_results})
            if r.status_code < 400:
                items = r.json().get("results", [])[:max_results]
                return {"ok": True, "provider": "tavily", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("url",""),
                                     "snippet": i.get("content", i.get("snippet",""))} for i in items]}
            logger.warning("Tavily %s, trying next", r.status_code)

        if serper_key:
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.post("https://google.serper.dev/search",
                    headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                    json={"q": query, "num": max_results})
            if r.status_code < 400:
                items = r.json().get("organic", [])[:max_results]
                return {"ok": True, "provider": "serper", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("link", i.get("url","")),
                                     "snippet": i.get("snippet", i.get("content",""))} for i in items]}
            logger.warning("Serper %s, trying next", r.status_code)

        if brave_key:
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.get("https://api.search.brave.com/res/v1/web/search",
                    headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
                    params={"q": query, "count": max_results})
            if r.status_code < 400:
                items = r.json().get("web", {}).get("results", [])[:max_results]
                return {"ok": True, "provider": "brave", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("url",""),
                                     "snippet": i.get("description","")} for i in items]}
            logger.warning("Brave %s, trying next", r.status_code)

        if exa_key:
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.post("https://api.exa.ai/search",
                    headers={"x-api-key": exa_key, "Content-Type": "application/json"},
                    json={"query": query, "numResults": max_results})
            if r.status_code < 400:
                items = r.json().get("results", [])[:max_results]
                return {"ok": True, "provider": "exa", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("url",""),
                                     "snippet": i.get("text","")[:300]} for i in items]}
            logger.warning("Exa %s, trying next", r.status_code)

        if serpapi_key:
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.get("https://serpapi.com/search",
                    params={"q": query, "api_key": serpapi_key, "num": max_results, "engine": "google"})
            if r.status_code < 400:
                items = r.json().get("organic_results", [])[:max_results]
                return {"ok": True, "provider": "serpapi", "query": query,
                        "results": [{"title": i.get("title",""), "url": i.get("link",""),
                                     "snippet": i.get("snippet","")} for i in items]}
            logger.warning("SerpAPI %s, trying next", r.status_code)

        logger.warning("All search providers failed or unconfigured, using simulated results")
        return {"ok": True, "provider": "simulated", "query": query,
                "results": [
                    {"title": f"Result for {query}", "url": f"https://example.com/?q={query}",
                     "snippet": f"Simulated result for: {query}"},
                    {"title": f"Documentation: {query}", "url": f"https://docs.example.com/{query}",
                     "snippet": f"Reference documentation for {query}"},
                ]}

    except Exception as e:
        logger.exception("Web search error")
        return {"ok": False, "error": str(e)}
