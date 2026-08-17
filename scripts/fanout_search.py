"""Multi-engine research query for humans/agents.

Runs a single query through the swarm's parallel web-search fan-out
(`swarm_os.lib.mcp.web_search.web_search_handler`) — every configured engine
(Tavily/Serper/Brave/Exa/SerpAPI/TinyFish) fires concurrently, results are
merged + deduped by URL, each tagged with its provider. Free (all keys are the
project's free/credit tiers).

Usage:
    .venv\\Scripts\\python.exe scripts/fanout_search.py "your question" [max_results]

Output: merged results as (provider) title | url | snippet, then a `json:` line
with the raw payload for programmatic consumers.
"""

import asyncio
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(ROOT, ".env"), override=True)
    except Exception:
        pass  # env vars already live in the process


async def main() -> int:
    _load_env()
    args = [a for a in sys.argv[1:] if a]
    if not args:
        print('usage: fanout_search.py "query" [max_results]', file=sys.stderr)
        return 2
    query = args[0]
    max_results = int(args[1]) if len(args) > 1 and args[1].isdigit() else 5

    from swarm_os.lib.mcp.web_search import web_search_handler

    t0 = time.monotonic()
    res = await web_search_handler({"query": query, "max_results": max_results})
    elapsed = time.monotonic() - t0

    print(f"# query: {query}")
    print(
        f"# elapsed: {elapsed:.1f}s  ok={res.get('ok')}  providers={res.get('providers')}"
    )
    if not res.get("ok"):
        print(f"# error: {res.get('error')}")
        return 1

    for i, r in enumerate(res.get("results", [])):
        print(f"[{i}] ({r.get('provider')}) {r.get('title', '').strip()}")
        print(f"     {r.get('url', '')}")
        snippet = (r.get("snippet") or "").strip().replace("\n", " ")
        if snippet:
            print(f"     {snippet[:220]}")
    print("# json:" + json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
