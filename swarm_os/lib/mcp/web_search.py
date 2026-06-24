from __future__ import annotations

import logging
import os
from typing import Any, Dict, List
import httpx

logger = logging.getLogger(__name__)

async def web_search_handler(params: Dict[str, Any], trace_hook=None) -> Dict[str, Any]:
    """
    Handles web search operations.
    Defaults to using a public search endpoint or a simulated one if no API keys are found.
    """
    query = params.get("query", "")
    max_results = int(params.get("max_results", 5))

    if not query:
        return {"ok": False, "error": "Search query is required"}

    try:
        logger.info(f"Performing web search for: {query}")

        tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
        serper_key = os.getenv("SERPER_API_KEY", "").strip()
        brave_key = os.getenv("BRAVE_API_KEY", "").strip()
        exa_key = os.getenv("EXA_API_KEY", "").strip()
        serpapi_key = os.getenv("SERPAPI_KEY", "").strip()
        tinyfish_key = os.getenv("TINYFISH_API_KEY", "").strip()

        if tavily_key:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": tavily_key, "query": query, "max_results": max_results},
                )
            if getattr(response, "status_code", 200) >= 400:
                return {"ok": False, "provider": "tavily", "error": f"HTTP {response.status_code}"}
            data = response.json()
            results = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", item.get("snippet", "")),
                }
                for item in data.get("results", [])[:max_results]
            ]
            if trace_hook:
                trace_hook("web_search", {"provider": "tavily", "query": query, "result_count": len(results)})
            return {"ok": True, "provider": "tavily", "query": query, "results": results}

        if serper_key:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                    json={"q": query, "num": max_results},
                )
            if getattr(response, "status_code", 200) >= 400:
                return {"ok": False, "provider": "serper", "error": f"HTTP {response.status_code}"}
            data = response.json()
            results = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("link", item.get("url", "")),
                    "snippet": item.get("snippet", item.get("content", "")),
                }
                for item in data.get("organic", [])[:max_results]
            ]
            if trace_hook:
                trace_hook("web_search", {"provider": "serper", "query": query, "result_count": len(results)})
            return {"ok": True, "provider": "serper", "query": query, "results": results}

        if brave_key:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
                    params={"q": query, "count": max_results},
                )
            if getattr(resp, "status_code", 200) < 400:
                data = resp.json()
                results = [{"title": r.get("title",""), "url": r.get("url",""), "snippet": r.get("description","")} for r in data.get("web",{}).get("results",[])[:max_results]]
                if trace_hook: trace_hook("web_search", {"provider": "brave", "query": query, "result_count": len(results)})
                return {"ok": True, "provider": "brave", "query": query, "results": results}

        if exa_key:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "https://api.exa.ai/search",
                    headers={"x-api-key": exa_key, "Content-Type": "application/json"},
                    json={"query": query, "numResults": max_results},
                )
            if getattr(resp, "status_code", 200) < 400:
                data = resp.json()
                results = [{"title": r.get("title",""), "url": r.get("url",""), "snippet": r.get("text","")[:300]} for r in data.get("results",[])[:max_results]]
                if trace_hook: trace_hook("web_search", {"provider": "exa", "query": query, "result_count": len(results)})
                return {"ok": True, "provider": "exa", "query": query, "results": results}

        if serpapi_key:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://serpapi.com/search",
                    params={"q": query, "api_key": serpapi_key, "num": max_results, "engine": "google"},
                )
            if getattr(resp, "status_code", 200) < 400:
                data = resp.json()
                results = [{"title": r.get("title",""), "url": r.get("link",""), "snippet": r.get("snippet","")} for r in data.get("organic_results",[])[:max_results]]
                if trace_hook: trace_hook("web_search", {"provider": "serpapi", "query": query, "result_count": len(results)})
                return {"ok": True, "provider": "serpapi", "query": query, "results": results}

        if brave_key:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
                    params={"q": query, "count": max_results},
                )
            if getattr(response, "status_code", 200) < 400:
                data = response.json()
                results = [
                    {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
                    for r in data.get("web", {}).get("results", [])[:max_results]
                ]
                if trace_hook:
                    trace_hook("web_search", {"provider": "brave", "query": query, "result_count": len(results)})
                return {"ok": True, "provider": "brave", "query": query, "results": results}

        if exa_key:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    "https://api.exa.ai/search",
                    headers={"x-api-key": exa_key, "Content-Type": "application/json"},
                    json={"query": query, "numResults": max_results},
                )
            if getattr(response, "status_code", 200) < 400:
                data = response.json()
                results = [
                    {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("text", "")[:300]}
                    for r in data.get("results", [])[:max_results]
                ]
                if trace_hook:
                    trace_hook("web_search", {"provider": "exa", "query": query, "result_count": len(results)})
                return {"ok": True, "provider": "exa", "query": query, "results": results}

        if serpapi_key:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    "https://serpapi.com/search",
                    params={"q": query, "api_key": serpapi_key, "num": max_results, "engine": "google"},
                )
            if getattr(response, "status_code", 200) < 400:
                data = response.json()
                results = [
                    {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
                    for r in data.get("organic_results", [])[:max_results]
                ]
                if trace_hook:
                    trace_hook("web_search", {"provider": "serpapi", "query": query, "result_count": len(results)})
                return {"ok": True, "provider": "serpapi", "query": query, "results": results}

        results = [
            {
                "title": f"Result for {query} - 1",
                "url": f"https://example.com/search?q={query}&n=1",
                "snippet": f"This is a simulated search result for the query: {query}. It provides relevant information regarding the topic."
            },
            {
                "title": f"Expert discussion on {query}",
                "url": f"https://expert-blog.org/{query}",
                "snippet": f"Exploring the nuances of {query} in modern software engineering and automation frameworks."
            },
             {
                "title": f"Official documentation: {query}",
                "url": f"https://docs.software.io/{query}",
                "snippet": f"The definitive guide to understanding and implementing {query} with best practices."
            }
        ]
        
        # Trim results
        results = results[:max_results]

        if trace_hook:
            trace_hook("web_search", {"query": query, "result_count": len(results)})

        return {
            "ok": True,
            "provider": "simulated",
            "query": query,
            "results": results,
            "engine": "simulated"
        }

    except Exception as e:
        logger.exception("Web search tool error")
        return {"ok": False, "error": str(e)}
