"""Rob's Lawyer — trial-specific transcript analysis for US v. Duncan, Rainford
& Locust (18 Cr. 289 (SHS), May 2019).

Ingests the real court-reporter transcript text files from data/legal/transcripts/
(the gitignored trial-files directory; sources are the "corrected" .txt copies
from OneDrive on the rob court folder) and produces the full page-grounded analysis:
per-day chronology, witness matrix, objections & rulings log, and Batson pass.

Usage:
  python scripts/legal_trial_analysis.py                # all transcripts in data/legal/transcripts
  python scripts/legal_trial_analysis.py --out PATH     # custom report path
  python scripts/legal_trial_analysis.py --file PATH... # specific files only

The report is derived entirely from the transcript text — it reports WHAT is on
a page and WHERE, never asserted legal significance (the same discipline as
build_analysis: pure, synchronous, offline, page-grounded).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swarm_os.services.legal.transcript_search import (  # noqa: E402
    TranscriptIndex,
    ingest_transcript_file,
)
from swarm_os.services.legal.transcript_analysis import build_analysis  # noqa: E402

CASE = "US v. Duncan / Rainford / Locust, 18 Cr. 289 (SHS)"
TRANSCRIPTS_DIR = Path("data/legal/transcripts")
DEFAULT_OUT = Path("data/legal/trial_analysis.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="report output path")
    parser.add_argument(
        "--files", nargs="*", default=None,
        help="specific transcript files (relative to data/legal/transcripts); "
             "default: every *.txt in the transcripts dir, sorted by name",
    )
    args = parser.parse_args()

    if args.files:
        files = [Path(TRANSCRIPTS_DIR / f) for f in args.files]
    else:
        files = sorted(TRANSCRIPTS_DIR.glob("*.txt"))

    if not files:
        print(f"No transcripts found in {TRANSCRIPTS_DIR}. "
              "Copy your trial transcript .txt files there first.", file=sys.stderr)
        sys.exit(1)

    indices = []
    for f in files:
        try:
            idx = ingest_transcript_file(f, case=CASE)
            print(f"  {f.name}: {len(idx.passages)} passages, "
                  f"{len({p.page for p in idx.passages})} pages, "
                  f"{len(idx.speakers())} speakers")
            indices.append(idx)
        except Exception as exc:
            print(f"  {f.name}: FAILED to parse — {type(exc).__name__}: {exc}",
                  file=sys.stderr)

    # Merge same-day segments (files sharing a YYYY-MM-DD prefix) into one
    # logical trial day — e.g. the 5/6/19 pre-trial day split across 4 PDFs.
    merged: list[TranscriptIndex] = []
    by_day: dict[str, list[TranscriptIndex]] = {}
    for idx in indices:
        day = Path(idx.source).name[:10] if idx.source else "misc"
        by_day.setdefault(day, []).append(idx)
    for day, day_indices in sorted(by_day.items()):
        if len(day_indices) == 1:
            merged.append(day_indices[0])
            continue
        combined = day_indices[0]
        combined.source = f"{day} (merged {len(day_indices)} segments)"
        for extra in day_indices[1:]:
            combined.passages.extend(extra.passages)
            combined.witness_names.update(extra.witness_names)
        merged.append(combined)

    if not merged:
        print("No transcripts could be parsed.", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out)
    build_analysis(merged, str(out_path))
    print(f"\nAnalysis written to {out_path}")
    print(f"  {len(merged)} day(s), {sum(len(i.passages) for i in merged)} passages total")


if __name__ == "__main__":
    main()
