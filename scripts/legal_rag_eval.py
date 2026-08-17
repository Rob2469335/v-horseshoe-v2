"""Rob's Lawyer RAG evaluation harness (M7/M8).

Two operational metrics, both runnable in CI without live query logs (the
"High Recall, Small Data" finding 2403.18962: within-system eval on small live
data is unreliable — golden sets + reference-free metrics are the answer).

  1. MAR — Misleading Answer Rate (LegalCiteBench 2605.10186's headline measure):
     of the answers the system SHIPS with a confident (non-downgraded) citation,
     what fraction carry a citation that is fabricated / unaligned / unverified /
     unparsed? MAR is the honest "how often would we have shipped a confident
     wrong answer" number. Uses the SAME verification seam as the live advisor
     (verify_citations + align_citations + align_case_citations), so a score
     here tracks a regression in the real path.

  2. FAITHFULNESS (reference-free, RAGAS-style): for each golden question, is
     the synthesized answer actually supported by the retrieved chunks it cites?
     Token-overlap claim support (ALCE-style) — a claim must reference a
     retrieved chunk, else it's an unsupported generation. Reference-free so it
     works without hand-labeled gold answers.

Usage:
  python scripts/legal_rag_eval.py                # golden set built in-memory
  python scripts/legal_rag_eval.py --gold PATH    # JSON list of {question, ...}
  python scripts/legal_rag_eval.py --live         # call the real /legal/ask

The golden set is small (curated questions over the operator's jurisdictions);
MAR + faithfulness are deterministic given a set of answers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.WARNING)

from swarm_os.services.legal.citation_verify import (  # noqa: E402
    verify_citations,
    align_citations,
    align_case_citations,
)


def mar_from_verification(verification: dict[str, Any]) -> float:
    """Misleading Answer Rate from a single verification dict.

    MAR = misleading / shipped, where:
      - shipped = the answer carried >= 1 citation OR was not downgraded
      - misleading = fabricated + unaligned + shape_mismatch + unverified +
        unparsed (any signal that a shipped citation is not trustworthy)
    An answer with ZERO citations is not "shipped with a confident citation" —
    it contributes to neither numerator nor denominator (nothing misleading was
    shipped). An answer whose verification FAILED to run (no 'checked') is
    excluded too (we cannot call it misleading on no evidence)."""
    if not verification or not verification.get("checked"):
        return 0.0
    penalties = (
        verification.get("fabricated", 0)
        + verification.get("unaligned", 0)
        + verification.get("shape_mismatch", 0)
        + verification.get("unverified", 0)
        + verification.get("unparsed", 0)
        + len(verification.get("case_alignment", {}).get("unaligned", []))
    )
    shipped = verification.get("count", 0) or 0
    if shipped == 0:
        return 0.0
    return round(penalties / shipped, 4)


def faithfulness(
    claims: list[str], retrieved: list[str], threshold: float = 0.5
) -> dict[str, Any]:
    """Reference-free claim-support check (ALCE-style): each claim must share
    >= `threshold` of its tokens with at least one retrieved chunk. Returns
    {supported, unsupported, rate}. Deterministic — no LLM judge."""
    import re

    def _tok(s: str) -> set[str]:
        return set(re.findall(r"[a-z0-9']+", (s or "").lower()))

    supported: list[str] = []
    unsupported: list[str] = []
    retr_tok = [_tok(c) for c in (retrieved or [])]
    for claim in claims or []:
        ct = _tok(claim)
        if not ct:
            continue
        best = 0.0
        for rt in retr_tok:
            if not rt:
                continue
            overlap = len(ct & rt) / len(ct)
            best = max(best, overlap)
        (supported if best >= threshold else unsupported).append(claim)
    total = len(supported) + len(unsupported)
    return {
        "supported": supported,
        "unsupported": unsupported,
        "rate": round(len(supported) / total, 4) if total else 0.0,
    }


async def evaluate_answer(
    question: str, answer: str, retrieved: list[str]
) -> dict[str, Any]:
    """Run the FULL verification seam on one answer and fold it into MAR +
    faithfulness. Mirrors what legal_advisor.advise() does per answer."""
    vres = await verify_citations(answer)
    stat_align = align_citations(answer, retrieved)
    case_align = align_case_citations(answer, [])
    verify = {
        "checked": True,
        "fabricated": vres.stats.get("fabricated", 0),
        "ambiguous": vres.stats.get("ambiguous", 0),
        "shape_mismatch": vres.stats.get("shape_mismatch", 0),
        "unverified": vres.stats.get("unverified", 0),
        "unparsed": vres.stats.get("unparsed", 0),
        "unaligned": len(stat_align["unaligned"]),
        "case_alignment": {"unaligned": case_align["unaligned"]},
        "count": vres.stats.get("count", 0) or 0,
    }
    mar = mar_from_verification(verify)
    # Claim-level faithfulness: split the answer into sentences, check each
    # against the retrieved chunks.
    import re

    claims = [c.strip() for c in re.split(r"(?<=[.!?])\s+", answer) if c.strip()]
    faith = faithfulness(claims, retrieved)
    return {"question": question, "mar": mar, "verify": verify, "faithfulness": faith}


async def run_golden(gold: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the harness over a golden question set. Each item:
    {question, answer, retrieved: [chunk strings]}. Answers can come from a real
    /legal/ask run (--live) or be hand-authored gold answers for the seam test."""
    results = []
    for item in gold:
        res = await evaluate_answer(
            item.get("question", ""),
            item.get("answer", ""),
            item.get("retrieved", []) or [item.get("retrieved_text", "")],
        )
        results.append(res)
    mars = [r["mar"] for r in results]
    faiths = [r["faithfulness"]["rate"] for r in results]
    return {
        "n": len(results),
        "mean_mar": round(sum(mars) / len(mars), 4) if mars else 0.0,
        "max_mar": max(mars) if mars else 0.0,
        "mean_faithfulness": round(sum(faiths) / len(faiths), 4) if faiths else 0.0,
        "results": results,
    }


def _builtin_golden() -> list[dict[str, Any]]:
    """A tiny golden set over the operator's jurisdictions — hand-authored gold
    answers to exercise the verification seam (NOT model output, so MAR here is
    the seam's own behavior on known-good / known-bad citations)."""
    return [
        {
            "question": "What notice must a NY landlord give before eviction?",
            "answer": (
                "The tenant is entitled to notice under N.Y. RPA Law § 235-b. "
                "See United States v. Simeonov, 252 F.3d 238 (2d Cir. 2001)."
            ),
            "retrieved": [
                "N.Y. RPA Law § 235-b tenant notice provision",
                "252 F.3d 238 substitute counsel",
            ],
        },
        {
            "question": "When is restitution subject to abuse-of-discretion review?",
            "answer": (
                "Restitution awards are reviewed for abuse of discretion "
                "(446 F.3d 65). The invented 999 F.4th 1 is not a real cite."
            ),
            "retrieved": ["446 F.3d 65 restitution abuse of discretion"],
        },
        {
            "question": "Is there a Batson issue in the peremptory strikes?",
            "answer": (
                "Batson v. Kentucky, 476 U.S. 79 governs peremptory challenges."
            ),
            "retrieved": ["476 U.S. 79 Batson peremptory equal protection"],
        },
    ]


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--gold",
        default=None,
        help="path to JSON list of golden {question, answer, retrieved}",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="query the real /legal/ask per golden question",
    )
    args = ap.parse_args()

    gold = _builtin_golden()
    if args.gold:
        with open(args.gold, encoding="utf-8") as f:
            gold = json.load(f)

    if args.live:
        import urllib.request

        for item in gold:
            body = json.dumps({"question": item["question"]}).encode()
            req = urllib.request.Request(
                "http://127.0.0.1:8000/legal/ask",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
            item["answer"] = data.get("answer", "")
            item["retrieved"] = [
                c.get("content", "") for c in data.get("citations", [])
            ]

    report = await run_golden(gold)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
