"""LegalCiteBench Cat3 evaluation harness for Rob's Lawyer (M4).

Runs BOTH deterministic verification seams against the REAL LegalCiteBench
Cat3-citation-verification corpus (Sijia711/LegalCiteBench; 5,474 rows, 50:50
3-fake / 3-true — a real case with volume/page/series altered vs unchanged).

WHAT THIS MEASURES (deterministic, offline, no external token):

  1. EXTRACTION RECALL — for every row, does the seam find the paragraph's case
     citation and form ONE stable canonical key? Cat3 covers the REAL paragraph
     shapes (including exotic Louisiana "10-11 (La.App. 5 Cir. 11/4/15)" forms),
     so a coverage gap here is a REAL findable finding in this codebase (M3's
     eyecite path never touched real Cat3 paragraphs).
2. FAKE-DETECTION SIGNAL — on 3-fake rows, does the paragraph carry a key
      OUTSIDE the corrected-key set that ground_truth names? Real Cat3 fake
      paragraphs contain BOTH the fabricated cite and the corrected one (e.g.
      "79 N.J. 254 (1979)"... "established in 79 N.J. 251"), so the detection
      condition is SET NON-MEMBERSHIP, NOT "GT key missing": flag any row whose
      keys include a citation that differs from the corrected set.

  NOT MEASURED (documented boundary, deliberately NOT conflated):
  - EXTERNAL field-alteration detection: with no COURT_LISTENER token the tool
    cannot KNOW "400 U.S. 79" is wrong vs "400 U.S. 74" — both are real-shaped.
    This harness measures the offline-parsable signal only; the external leg
    exists in citation_verify (token-gated) and fills the residual.
  - true-rows: GT is "There is no error" -> there is no corrected key to compare
    textually; they contribute to extraction recall, not to a disagree score.

  LIVE-PATH NOTE (fail-closed, see citation_verify + legal_advisor): the rows
  this harness counts as "unparsed"/"GT-unparseable (external leg required)"
  are NOT silently clean in the running system — verify_citations reports them
  as stats["unparsed"] (citation-shaped but unparseable) and stats["unverified"]
  (parsed, no verdict), and legal_advisor.advise() APPENDS a [VERIFICATION]
  "Do not rely on this answer until checked" downgrade whenever either is > 0.
  The live path therefore surfaces the 31 not-detected rows instead of passing
  them as "0 citation issues".

Usage:
  python scripts/legal_citebench_eval.py [--path PATH] [--sample N]
  --path   path to a cat3 jsonl (default: auto — GitHub raw, else temp fallback)
  --sample cap rows to process (default: all)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.getLogger("eyecite").setLevel(logging.ERROR)

from swarm_os.services.legal.citation_verify import (  # noqa: E402
    _resolve_to_full,
    case_citation_key,
    extract_statute_sections,
)

_CAT3_URL = (
    "https://raw.githubusercontent.com/Sijia711/LegalCiteBench/master/data/"
    "cat3/cat3_citation_verification.jsonl"
)
_LOCAL_FALLBACK = Path(
    r"C:\Users\rober\AppData\Local\Temp\cat3_citation_verification.jsonl"
)


def read_cat3(path: str | None) -> list[dict]:
    if path and Path(path).exists():
        dest = Path(path)
    elif _LOCAL_FALLBACK.exists():
        dest = _LOCAL_FALLBACK
    else:
        tmp = Path(__file__).resolve().parents[1] / "data" / "legal"
        tmp.mkdir(parents=True, exist_ok=True)
        dest = tmp / "cat3_citation_verification.jsonl"
        print(f"downloading Cat3 -> {dest}")
        with urllib.request.urlopen(_CAT3_URL, timeout=180) as resp:
            dest.write_bytes(resp.read())
    rows = []
    for line in dest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def paragraph_case_keys(question: str) -> list[str]:
    """Canonical keys of every FullCaseCitation eyecite finds in a passage."""
    strings, kinds = _resolve_to_full(question)
    out = []
    for s, k in zip(strings, kinds):
        if k == "FullCaseCitation":
            key = case_citation_key(s)
            if key:
                out.append(key)
    return out


def gt_corrected_key(ground_truth: str) -> str | None:
    """Extract the corrected-citation key from a 3-fake ground truth line."""
    gt = (ground_truth or "").strip()
    if not gt:
        return None
    for prefix in (
        "the citation is incorrect. the correct citation is: ",
        "the citation is incorrect. the correct citation is ",
        "the citation is incorrect, the correct citation is: ",
        "the citation is incorrect, the correct citation is ",
    ):
        if gt.lower().startswith(prefix):
            frag = gt[len(prefix) :].strip().strip(".")
            for key in (frag, frag.split("(", 1)[0].strip()):
                ck = case_citation_key(key)
                if ck:
                    return ck
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--path", default=None, help="path to a cat3 jsonl (default: auto)")
    ap.add_argument(
        "--sample", type=int, default=0, help="cap a subset for a smoke run"
    )
    args = ap.parse_args()

    rows = read_cat3(args.path)
    if args.sample:
        rows = rows[: args.sample]
    print(f"[legal-citebench-eval] rows loaded: {len(rows)}")

    style = Counter(r.get("qa_style") for r in rows)
    print(f"[legal-citebench-eval] qa_style: {dict(style)}")

    par_cov = Counter()
    stat_cov = Counter()
    fake_ids: list[str] = []
    agree_ids: list[str] = []
    miss_ids: list[str] = []
    for r in rows:
        jstyle = r.get("qa_style")
        q = r.get("question") or ""
        keys = paragraph_case_keys(q)
        # Statutory §-sections the deterministic extractor finds in the passage
        # (M4's seam — the corpus is statutes, and eyecite mangles them).
        if extract_statute_sections(q):
            stat_cov[f"{jstyle}_statute_sections"] += 1
        if keys:
            par_cov[f"{jstyle}_parsed"] += 1
            if jstyle == "3-fake":
                gkey = gt_corrected_key(r.get("ground_truth") or "")
                if gkey is not None:
                    par_cov[f"{jstyle}_gtkey"] += 1
                    if any(k != gkey for k in keys):
                        fake_ids.append(r.get("id"))
                    else:
                        # All paragraph keys equal the corrected key — the
                        # fabricated cite was NOT picked up (or was normalized
                        # away). Genuine false-negative candidate.
                        agree_ids.append(r.get("id"))
                else:
                    # GT corrected unparseable (e.g. "3 Cir" partial) — the
                    # external leg is required for that row; count honestly.
                    par_cov[f"{jstyle}_gt_unparseable"] += 1
        else:
            par_cov[f"{jstyle}_unparsed"] += 1
            if jstyle == "3-fake":
                gkey = gt_corrected_key(r.get("ground_truth") or "")
                if gkey is not None:
                    miss_ids.append(r.get("id"))

    print("[legal-citebench-eval] coverage (paragraph keys):")
    for k in sorted(par_cov):
        print(f"    {k}: {par_cov[k]}")
    print("[legal-citebench-eval] statutory-section coverage (deterministic seam):")
    for k in sorted(stat_cov):
        print(f"    {k}: {stat_cov[k]}")
    tp = len(fake_ids)
    miss_total = len(agree_ids) + len(miss_ids) + par_cov["3-fake_gt_unparseable"]
    print(
        f"[legal-citebench-eval] detection signal (3-fake, {style['3-fake']} rows): "
        f"{tp} DETECTED (paragraph carries a citation key != corrected set); "
        f"{len(agree_ids)} false-negative (only corrected key present); "
        f"{len(miss_ids)} unparsed; "
        f"{par_cov.get('3-fake_gt_unparseable', 0)} GT-unparseable (external leg required); "
        f"{miss_total} total not detected."
    )
    print(f"[legal-citebench-eval] false-negative ids: {agree_ids[:5]}")
    print(f"[legal-citebench-eval] unparsed ids: {miss_ids[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
