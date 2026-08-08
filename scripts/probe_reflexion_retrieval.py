"""Manual diagnostic probe: does reflexion-memory retrieval surface the RIGHT
precedent for a real failure?

2026-08-07. Validates the move-5 fix end-to-end against real stored data:
  - dense search (gte-modernbert, :8081) pulls the top-5 ReflexionMemory candidates
  - the cross-encoder reranker (gte-reranker-modernbert, :8082) reorders them
  - the rerank score is PRIMARY; recency/confidence is a filter+tiebreak

WHY THIS MATTERS (distinction worth remembering):
  dense search usually gets the TOP match right. The fix does not usually
  "correct a wrong #1" — it "rescues buried-but-relevant precedents": past the
  obvious top hit, dense cannot tell a second relevant precedent apart from
  irrelevant noise (a second `File not found` rule can sit at 0.028, tied with
  unrelated LLM-failure rules). check_for_past_mistakes uses limit=5, so those
  buried relevant picks were silently lost. The reranker separates them.

Run against a real failure from each category. Usage:
  python scripts/probe_reflexion_retrieval.py [query ...]
  (no query -> uses the default filesystem-not-found failure)
Requires the stack up (:8081 embed, :8082 rerank, :6333 qdrant).
"""
import asyncio
import sys

import requests
from qdrant_client import AsyncQdrantClient

EMBED_URL = "http://127.0.0.1:8081"
RERANK_URL = "http://127.0.0.1:8082"
EMBED_MODEL = "gte-modernbert-base-Q8_0.gguf"
RERANK_MODEL = "gte-reranker-modernbert-base-Q8_0.gguf"

DEFAULT_QUERY = "agent:debugger analyzing codebase failed: File 'C:/x/src/app.py' not found, the path does not exist, cannot read it"

# One real failure per reflexion category, so the probe can be run across the
# range of failure classes the store actually contains (not just filesystem).
CATEGORY_QUERIES = {
    "filesystem": DEFAULT_QUERY,
    "llm_tool_decision": "agent:code_analyzer tool decision failed after 3 attempts: litellm.BadRequestError the OpenAI provider rejected the request",
    "sandbox_security": "agent:coder sandbox_repl blocked: Security Gate banned the module import, execution refused for the python snippet",
}


async def probe(query: str) -> dict:
    c = AsyncQdrantClient(url="http://127.0.0.1:6333", timeout=10)
    r = requests.post(f"{EMBED_URL}/v1/embeddings",
                      headers={"Authorization": "Bearer llama"},
                      json={"model": EMBED_MODEL, "input": query}, timeout=30)
    vec = r.json()["data"][0]["embedding"]

    q = await c.query_points(collection_name="ReflexionMemory", query=vec, limit=5)
    dense = list(getattr(q, "points", q))
    await c.close()
    if not dense:
        return {"query": query, "dense": [], "reranked": []}

    dense_rows = []
    for p in dense:
        pl = p.payload or {}
        dense_rows.append({
            "id": p.id,
            "dense": round(float(p.score), 3),
            "reason": str(pl.get("failure_reason", ""))[:55],
            "correction": str(pl.get("correction", ""))[:70],
            "confidence": pl.get("confidence"),
        })

    texts = [str((p.payload or {}).get("correction", "")) or str((p.payload or {}).get("failure_reason", "")) for p in dense]
    rr = requests.post(f"{RERANK_URL}/v1/rerank",
                       headers={"Authorization": "Bearer llama"},
                       json={"model": RERANK_MODEL, "query": query, "documents": texts, "top_n": 5}, timeout=30)
    rres = sorted(rr.json()["results"], key=lambda x: -x["relevance_score"])
    reranked_rows = []
    for item in rres:
        d = dense_rows[item["index"]]
        reranked_rows.append({"rerank": round(item["relevance_score"], 2), **d})
    return {"query": query, "dense": dense_rows, "reranked": reranked_rows}


def show(result: dict) -> None:
    print(f"\nQUERY: {result['query'][:90]}")
    print("  DENSE top-5 (dense-nearest first):")
    for d in result["dense"]:
        print(f"    dense={d['dense']:.3f} [{d['id'][:8]}] {d['reason']} || {d['correction']}")
    print("  RERANKED (same 5, rerank primary):")
    for d in result["reranked"]:
        print(f"    rerank={d['rerank']:+.2f} (dense {d['dense']:.3f}) {d['reason']} || {d['correction']}")


async def main() -> None:
    args = [a for a in sys.argv[1:]]
    if args:
        for a in args:
            show(await probe(a))
    else:
        for name, q in CATEGORY_QUERIES.items():
            print(f"\n### {name} category")
            show(await probe(q))


if __name__ == "__main__":
    asyncio.run(main())
