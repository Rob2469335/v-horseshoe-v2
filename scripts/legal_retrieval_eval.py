"""Rob's Lawyer retrieval evaluation (MLEB-style, local golden set).

Evidence: the GTE-modernbert embedder + BGE reranker have ZERO legal-domain
retrieval numbers (their HF cards report MTEB/BEIR/CoIR only) — MLEB
(arXiv:2510.19365) exists precisely to fill that hole, but its datasets are
large/remote. This script is the LOCAL, offline analog: a curated golden set of
legal questions over the operator's own corpus, each with the known-relevant
citations. It measures recall@K (did the hybrid pipeline surface the authority
that actually answers the question) and mean reciprocal rank (MRR) — the two
metrics that decide whether the retrieval layer is silently leaking recall.

The golden set is small but DELIBERATELY adversarial: it includes (a) topic-
described questions that name NO citation (dense/context must do the work),
(b) exact-citation queries (BM25 must do the work), and (c) compound questions.
Recall@K on this set is the "is my embedder+rerank actually good for law" test.

Usage:
  python scripts/legal_retrieval_eval.py           # run all golden queries
  python scripts/legal_retrieval_eval.py --gold X  # custom JSON golden set

Golden item shape: {question, jurisdiction, expected_cites: [str, ...],
                    corpus: "statutes"|"cases"}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.WARNING)

from swarm_os.services.legal.legal_search import search_statutes, search_cases  # noqa: E402
from swarm_os.services.legal.citation_verify import case_citation_key  # noqa: E402

GOLDEN = [
    {
        "question": "What notice must a New York landlord give before withholding a security deposit?",
        "jurisdiction": "ny", "corpus": "statutes",
        "expected_cites": ["N.Y. GOL Law § 7-103"],
    },
    {
        "question": "tenant rights when the apartment becomes uninhabitable",
        "jurisdiction": "ny", "corpus": "statutes",
        "expected_cites": [],  # topic-described, no exact cite — any relevant hit counts
        "expected_keywords": ["warranty", "habitability"],
    },
    {
        "question": "what is the standard for plain error review of an unpreserved objection",
        "jurisdiction": "federal", "corpus": "cases",
        "expected_cites": ["507 U.S. 725"],
    },
    {
        "question": "Batson peremptory challenge racial discrimination equal protection",
        "jurisdiction": "federal", "corpus": "cases",
        "expected_cites": ["476 U.S. 79"],
    },
    {
        "question": "restitution award abuse of discretion review standard",
        "jurisdiction": "federal", "corpus": "cases",
        "expected_cites": ["446 F.3d 65"],
    },
    {
        "question": "substitute counsel waiver 252 F.3d 238",
        "jurisdiction": "federal", "corpus": "cases",
        "expected_cites": ["252 F.3d 238"],
    },
]


def _normalize_cite(cite: str) -> str:
    """Canonical key for a citation string (case) or statute section."""
    k = case_citation_key(cite)
    if k:
        return k
    return cite.lower().strip()


async def evaluate_item(item: dict, top_k: int = 5) -> dict:
    """Run the hybrid retrieval for one golden item and compute whether the
    expected authority was surfaced. Returns recall@K + MRR contribution."""
    q = item["question"]
    corpus = item.get("corpus", "statutes")
    expected = [_normalize_cite(c) for c in item.get("expected_cites", [])]
    expected_keywords = item.get("expected_keywords", [])

    if corpus == "cases":
        results = await search_cases(q, top_k=top_k)
        hit_field = "citation"
    else:
        results = await search_statutes(q, jurisdiction=item.get("jurisdiction"), top_k=top_k)
        hit_field = "citation"

    surfaced = [_normalize_cite(r.get(hit_field, "") or "") for r in results]
    # Recall@K: of the expected citations, how many surfaced in the top-k?
    recall = 0.0
    mrr_sum = 0.0
    if expected:
        found = 0
        for rank, s in enumerate(surfaced, 1):
            for exp in expected:
                if s and (s == exp or exp in s or s in exp):
                    found += 1
                    mrr_sum += 1.0 / rank
                    break
            if found >= len(expected):
                break
        recall = found / len(expected)
        mrr = mrr_sum / len(expected)
    elif expected_keywords:
        # Topic-described with no exact cite: score by keyword presence in the
        # top result's content.
        recall = 1.0 if results and any(
            kw.lower() in (results[0].get("content", "") or "").lower()
            for kw in expected_keywords
        ) else 0.0
        mrr = 1.0 if recall else 0.0
    else:
        recall, mrr = 1.0, 1.0  # nothing expected -> trivially satisfied

    return {
        "question": q, "corpus": corpus, "surfaced": surfaced,
        "expected": item.get("expected_cites", []), "recall@k": recall,
        "mrr": mrr, "top_result": (results[0].get(hit_field, "") if results else ""),
    }


async def run_eval(gold: list[dict], top_k: int = 5) -> dict:
    results = []
    for item in gold:
        try:
            results.append(await evaluate_item(item, top_k=top_k))
        except Exception as exc:
            results.append({"question": item.get("question", ""), "error": str(exc),
                            "recall@k": 0.0, "mrr": 0.0, "surfaced": []})
    recalls = [r["recall@k"] for r in results]
    mrrs = [r["mrr"] for r in results]
    return {
        "n": len(results),
        "mean_recall@k": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "mean_mrr": round(sum(mrrs) / len(mrrs), 4) if mrrs else 0.0,
        "results": results,
    }


def render_report(report: dict) -> str:
    out = [f"# Legal Retrieval Eval — mean recall@{len(report.get('results', [])) and 5}: {report['mean_recall@k']}",
           f"MRR: {report['mean_mrr']}"]
    for r in report.get("results", []):
        if r.get("error"):
            out.append(f"- [ERR] {r['question']}: {r['error']}")
            continue
        top = r.get("top_result", "")
        out.append(f"- [{r['corpus']}] recall={r['recall@k']:.2f} mrr={r['mrr']:.2f} "
                   f"| {r['question'][:60]} | top={top[:40]} | expected={r['expected']}")
    return "\n".join(out)


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=None)
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()
    gold = GOLDEN
    if args.gold:
        with open(args.gold, encoding="utf-8") as f:
            gold = json.load(f)
    report = await run_eval(gold, top_k=args.top_k)
    print(json.dumps(report, indent=2))
    print("\n" + render_report(report))


if __name__ == "__main__":
    asyncio.run(_main())
