from __future__ import annotations

import os
import logging
import urllib.parse
from typing import Any, Dict, List
import httpx
import re

logger = logging.getLogger(__name__)

async def web_search_handler(params: Dict[str, Any], trace_hook=None) -> Dict[str, Any]:
    """
    Handles web search operations using Tavily, Serper, Brave, SerpApi,
    Exa, Tinyfish, or falls back to a custom DuckDuckGo HTML parser.
    """
    query = params.get("query", "").strip()
    max_results = int(params.get("max_results", 5))

    if not query:
        return {"ok": False, "error": "Search query is required"}

    # 1. Try Tavily Search API
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        try:
            logger.info("Performing Tavily Search API call...")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": tavily_key, "query": query, "max_results": max_results}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    raw_results = data.get("results", [])
                    results = []
                    for r in raw_results:
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("content", "")
                        })
                    if trace_hook:
                        trace_hook("web_search", {"provider": "tavily", "query": query, "count": len(results)})
                    return {"ok": True, "query": query, "results": results, "provider": "tavily"}
                else:
                    logger.warning(f"Tavily API failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Tavily Search API exception: {e}")

    # 2. Try Serper (Google Search) API
    serper_key = os.environ.get("SERPER_API_KEY")
    if serper_key:
        try:
            logger.info("Performing Serper API call...")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                    json={"q": query, "num": max_results}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    raw_results = data.get("organic", [])
                    results = []
                    for r in raw_results:
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("link", ""),
                            "snippet": r.get("snippet", "")
                        })
                    if trace_hook:
                        trace_hook("web_search", {"provider": "serper", "query": query, "count": len(results)})
                    return {"ok": True, "query": query, "results": results, "provider": "serper"}
                else:
                    logger.warning(f"Serper API failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Serper API exception: {e}")

    # 3. Try Brave Search API
    brave_key = os.environ.get("BRAVE_API_KEY")
    if brave_key:
        try:
            logger.info("Performing Brave Search API call...")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={"X-Subscription-Token": brave_key, "Accept": "application/json"},
                    params={"q": query, "count": max_results}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    raw_results = data.get("web", {}).get("results", [])
                    results = []
                    for r in raw_results:
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("description", "")
                        })
                    if trace_hook:
                        trace_hook("web_search", {"provider": "brave", "query": query, "count": len(results)})
                    return {"ok": True, "query": query, "results": results, "provider": "brave"}
                else:
                    logger.warning(f"Brave API failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Brave API exception: {e}")

    # 4. Try SerpApi (Google Search) API
    serpapi_key = os.environ.get("SERPAPI_KEY")
    if serpapi_key:
        try:
            logger.info("Performing SerpApi Search call...")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://serpapi.com/search.json",
                    params={"q": query, "api_key": serpapi_key, "num": max_results}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    raw_results = data.get("organic_results", [])
                    results = []
                    for r in raw_results:
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("link", ""),
                            "snippet": r.get("snippet", "")
                        })
                    if trace_hook:
                        trace_hook("web_search", {"provider": "serpapi", "query": query, "count": len(results)})
                    return {"ok": True, "query": query, "results": results, "provider": "serpapi"}
                else:
                    logger.warning(f"SerpApi failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"SerpApi exception: {e}")

    # 5. Try Exa AI Search API
    exa_key = os.environ.get("EXA_API_KEY")
    if exa_key:
        try:
            logger.info("Performing Exa AI Search call...")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.exa.ai/search",
                    headers={"x-api-key": exa_key, "Content-Type": "application/json"},
                    json={"query": query, "numResults": max_results, "useAutoprompt": True}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    raw_results = data.get("results", [])
                    results = []
                    for r in raw_results:
                        # Fetch description/snippet from text or highlight
                        snippet = r.get("text", "")
                        if not snippet and r.get("highlights"):
                            snippet = r.get("highlights")[0]
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": snippet[:300] if snippet else ""
                        })
                    if trace_hook:
                        trace_hook("web_search", {"provider": "exa", "query": query, "count": len(results)})
                    return {"ok": True, "query": query, "results": results, "provider": "exa"}
                else:
                    logger.warning(f"Exa AI failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Exa AI exception: {e}")

    # 6. Try Tinyfish Search API
    tinyfish_key = os.environ.get("TINYFISH_API_KEY")
    if tinyfish_key:
        try:
            logger.info("Performing Tinyfish Search call...")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.search.tinyfish.ai",
                    headers={"X-API-Key": tinyfish_key},
                    params={"query": query, "location": "US", "language": "en"}
                )
                if resp.status_code == 200:
                    # Tinyfish returns a list of results directly or in a nested "results" property.
                    data = resp.json()
                    raw_results = data if isinstance(data, list) else data.get("results", [])
                    results = []
                    for r in raw_results:
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("snippet", "")
                        })
                    results = results[:max_results]
                    if trace_hook:
                        trace_hook("web_search", {"provider": "tinyfish", "query": query, "count": len(results)})
                    return {"ok": True, "query": query, "results": results, "provider": "tinyfish"}
                else:
                    logger.warning(f"Tinyfish failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Tinyfish exception: {e}")

    # 7. Fallback: DuckDuckGo HTML Scraper
    try:
        logger.info("No active API keys responded. Falling back to DuckDuckGo HTML parser...")
        async with httpx.AsyncClient(timeout=15.0) as client:
            encoded_query = urllib.parse.quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return {"ok": False, "error": f"DuckDuckGo fallback failed with status {resp.status_code}"}
            
            html = resp.text
            # Extract search result blocks using regular expressions
            results: List[Dict[str, str]] = []
            
            # Simple regex parser to extract DuckDuckGo search result links and descriptions
            blocks = re.findall(r'<div class="result__body">.*?</div>\s*</div>', html, re.DOTALL)
            for block in blocks:
                url_match = re.search(r'<a class="result__url"[^>]*href="([^"]+)"', block)
                title_match = re.search(r'<a class="result__snippet"[^>]*>([^<]+)</a>', block)
                if not title_match:
                    title_match = re.search(r'<a class="result__snippet"[^>]*href="[^"]+">([^<]+)</a>', block)
                snippet_match = re.search(r'<a class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
                
                # Check for alternative formats
                if not url_match:
                    url_match = re.search(r'<a class="result__snippet"[^>]*href="([^"]+)"', block)
                
                # Clean up match
                if url_match:
                    full_url = url_match.group(1)
                    if "/l/?uddg=" in full_url:
                        m = re.search(r'uddg=([^&]+)', full_url)
                        if m:
                            full_url = urllib.parse.unquote(m.group(1))
                    
                    title = ""
                    title_search = re.search(r'<a class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
                    if title_search:
                        title = re.sub(r'<[^>]+>', '', title_search.group(1)).strip()
                    
                    snippet = ""
                    snippet_search = re.search(r'<a class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
                    if snippet_search:
                        snippet = re.sub(r'<[^>]+>', '', snippet_search.group(1)).strip()
                    
                    if full_url and title:
                        results.append({
                            "title": title,
                            "url": full_url,
                            "snippet": snippet
                        })
                        if len(results) >= max_results:
                            break

            if trace_hook:
                trace_hook("web_search", {"provider": "duckduckgo_fallback", "query": query, "count": len(results)})

            return {
                "ok": True,
                "query": query,
                "results": results,
                "provider": "duckduckgo_fallback"
            }

    except Exception as e:
        logger.exception("DuckDuckGo fallback search tool error")
        return {"ok": False, "error": str(e)}
