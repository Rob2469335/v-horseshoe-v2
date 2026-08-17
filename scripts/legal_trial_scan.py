"""Rob's Lawyer — reproducible trial fault scan (US v. Duncan, Rainford & Locust,
18 Cr. 289 (SHS), May 2019).

A RECORD SCAN over the defendant's own trial transcripts in
data/legal/transcripts (gitignored). It reports WHAT the transcript shows and
WHERE (page cites), and the legal questions the shapes raise for a qualified
attorney. It never concludes that counsel was ineffective or that the government
tampered with evidence — those are legal conclusions for a qualified attorney.

The output is structured so a reader can always tell the difference between
  A. what the transcript establishes (exact pages, speaker, question/answer),
  B. what is potentially significant (incomplete evidence, deleted/recovered
     communications, chain-of-custody circumstances, counsel's response),
  C. what still has to be established (whether material existed, was favorable,
     was suppressed, whether counsel knew, whether there was a strategic reason,
     whether any omission caused prejudice).

Usage:
  python scripts/legal_trial_scan.py [--day YYYY-MM-DD] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swarm_os.services.legal.trial_advisor import (  # noqa: E402
    _load_indices,
    build_attorney_profiles,
    build_error_flags,
    build_key_events,
    build_phone_evidence_events,
    search_record,
)

DEFENSE_ATTORNEYS = ("MR. DINNERSTEIN", "MR. CECUTTI", "MS. AL-SHABAZZ", "MR. SCHOLAR")


def scan(day: str | None = None) -> dict:
    idx = _load_indices()
    if day:
        idx = [i for i in idx if day in (Path(i.source).name if i.source else "")]
    report: dict = {
        "loaded_days": len(idx),
        "total_passages": sum(len(i.passages) for i in idx),
    }

    # 1. Per-attorney profile
    profiles = build_attorney_profiles(idx)
    report["attorneys"] = {}
    for key in DEFENSE_ATTORNEYS:
        p = profiles[key]
        report["attorneys"][key] = {
            "name": p.name,
            "represents": p.represents,
            "words": p.word_count,
            "objections": len(p.objections),
            "examinations": len(p.examinations),
            "page_range": p.page_range,
            "sample_objections": p.objections[:6],
        }

    # 2. Defense objections (preserved-error picture)
    report["defense_objections"] = {}
    for key in DEFENSE_ATTORNEYS:
        report["defense_objections"][key] = {
            "count": len(profiles[key].objections),
            "sample": profiles[key].objections[:6],
        }

    # 3. Record patterns by category
    flags = build_error_flags(idx)
    cat_counts = Counter(f["category"] for f in flags)
    report["error_flags_by_category"] = dict(cat_counts.most_common())
    report["error_flags"] = flags

    # 4. Phone / message evidence events
    report["phone_evidence_events"] = build_phone_evidence_events(idx)

    # 5. Key events (chain of custody / evidence handling)
    report["key_events"] = build_key_events(idx)

    # 6. Targeted searches
    report["targeted_searches"] = {}
    for term in [
        "Brady",
        "chain of custody",
        "the phone was powered",
        "deleted",
        "selectively left out",
        "read and delete",
    ]:
        report["targeted_searches"][term] = search_record(idx, term, limit=6)
    return report


def render(report: dict) -> str:
    lines = []
    lines.append(
        f"Trial fault scan — {report['loaded_days']} days, "
        f"{report['total_passages']} passages"
    )
    lines.append("")

    lines.append("=" * 70)
    lines.append("1. COUNSEL ACTIVITY OVERVIEW")
    lines.append("=" * 70)
    for key, p in report["attorneys"].items():
        lines.append(f"\n{p['name']} — represents {p['represents']}")
        lines.append(
            f"  words={p['words']:,}  objections={p['objections']}  "
            f"exams={p['examinations']}  pages={p['page_range']}"
        )

    lines.append("\n" + "=" * 70)
    lines.append("2. DEFENSE OBJECTIONS (preserved-error potential)")
    lines.append("=" * 70)
    for key, o in report["defense_objections"].items():
        lines.append(f"\n{key} ({o['count']} total):")
        for obj in o["sample"]:
            lines.append(f"  p.{obj['page']}: {obj['text'][:100]}")

    lines.append("\n" + "=" * 70)
    lines.append("3. RECORD PATTERNS BY CATEGORY")
    lines.append("=" * 70)
    for cat, n in report["error_flags_by_category"].items():
        lines.append(f"  {cat}: {n} passages")

    lines.append("\n" + "=" * 70)
    lines.append("4. PHONE / MESSAGE EVIDENCE EVENTS")
    lines.append("=" * 70)
    for e in report["phone_evidence_events"]:
        lines.append(f"\n  p.{e['page']} [{e['speaker']}] day {e['day']}")
        lines.append(f"    {e['text'][:180]}")
        lines.append(f"    LEGAL Q: {e['legal_question']}")

    lines.append("\n" + "=" * 70)
    lines.append("5. KEY EVENTS (chain of custody / evidence handling)")
    lines.append("=" * 70)
    for e in report["key_events"]:
        lines.append(f"\n  [{e['category']}] pages {e['pages']} day {e['day']}")
        lines.append(f"    {e['note'][:200]}")

    lines.append("\n" + "=" * 70)
    lines.append("6. TARGETED SEARCHES")
    lines.append("=" * 70)
    for term, hits in report["targeted_searches"].items():
        lines.append(f"\n  '{term}': {len(hits)} hits")
        for h in hits[:3]:
            lines.append(f"    p.{h['page']} [{h['speaker']}]: {h['text'][:120]}")

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Trial fault scan (Rob's Lawyer)")
    ap.add_argument("--day", default=None, help="filter to one YYYY-MM-DD day")
    ap.add_argument("--json", default=None, help="write the raw report to a JSON file")
    ap.add_argument("--out", default=None, help="write the rendered report to a file")
    args = ap.parse_args()

    report = scan(day=args.day)
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        print(f"JSON report written to {args.json}")
    text = render(report)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Report written to {args.out}")
    print(text)


if __name__ == "__main__":
    main()
