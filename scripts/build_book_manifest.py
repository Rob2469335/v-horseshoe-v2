"""Build data/books/manifest.json from the expert digest.

Deterministic + idempotent: parses data/books/expert-digest.md (the canonical
human-authored source of truth) into per-book structured records, then merges
the hand-authored LEGAL_SOURCES table (legitimate free-access route + copyright
status per the 50-book acquisition plan — data, not policy).

Output schema (per book):
  slug, title, author, track, track_label, priority, scores{ai,income,
  technical,business,apply,long}, legitimate_source, public_domain,
  summary_status, ai_relevance, best_parts[], warnings[], freelancer_translation

Run with the project venv:
  .venv\\Scripts\\python.exe scripts/build_book_manifest.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIGEST = ROOT / "data" / "books" / "expert-digest.md"
OUT = ROOT / "data" / "books" / "manifest.json"

HEADER_RE = re.compile(r"^### (?:\d+\.?|30[ab]\.) (.+?) — (.+)$")
SCORE_RE = re.compile(
    r"\*\*\[(?P<priority>[A-Z ]+)\]\*\*"
    r"\s+ai:(?P<ai>\d+)\s+income:(?P<income>\d+)\s+technical:(?P<technical>\d+)"
    r"\s+business:(?P<business>\d+)\s+apply:(?P<apply>\d+)\s+long:(?P<long>\d+)"
)
BEST_RE = re.compile(r"^- ✓ (.*)$")
WARN_RE = re.compile(r"^- ⚠️ (.*)$")
TRANS_RE = re.compile(r"^- ↔ (.*)$")

# Hand-authored legitimate-access metadata (from the 50-book acquisition plan).
# key: (track, title-lowercase). public_domain=True only where established US
# public-domain status is confidently known; everything else defaults False.
LEGAL_SOURCES: dict[tuple[str, str], dict] = {
    ("investing", "security analysis"): {
        "legitimate_source": "Public-domain / older editions may be available",
        "public_domain": False,
    },
    ("investing", "reminiscences of a stock operator"): {
        "legitimate_source": "Older editions / public-domain-status research",
        "public_domain": False,
    },
}


def _legal(track: str, title: str) -> dict:
    key = (track, title.lower())
    return LEGAL_SOURCES.get(key, {
        "legitimate_source": "Library / summary",
        "public_domain": False,
    })


def parse_digest() -> list[dict]:
    text = DIGEST.read_text(encoding="utf-8")
    lines = text.splitlines()
    books: list[dict] = []
    track = ""
    track_label = ""
    for line in lines:
        if line.startswith("## TRACK"):
            m = re.match(r"^## TRACK \d+ — (.*) \((\d+)\)$", line)
            if m:
                track = m.group(1).lower().split("/")[0].strip()
                track_label = m.group(1).strip()
            continue
        hm = HEADER_RE.match(line)
        if not hm:
            continue
        title, author = hm.group(1).strip(), hm.group(2).strip()
        books.append({
            "title": title,
            "author": author,
            "track": track,
            "track_label": track_label,
            "best_parts": [],
            "warnings": [],
            "freelancer_translation": "",
        })
    # second pass: attach per-book content lines (bullets belong to last header)
    idx = -1
    for line in lines:
        hm = HEADER_RE.match(line)
        if hm:
            idx = min(idx + 1, len(books) - 1)
            continue
        if idx < 0:
            continue
        sm = SCORE_RE.search(line)
        if sm:
            books[idx]["priority"] = sm.group("priority").strip()
            books[idx]["scores"] = {k: int(v) for k, v in sm.groupdict().items()
                                    if k != "priority"}
            continue
        bm = BEST_RE.match(line)
        if bm:
            books[idx]["best_parts"].append(bm.group(1).strip())
            continue
        wm = WARN_RE.match(line)
        if wm:
            books[idx]["warnings"].append(wm.group(1).strip())
            continue
        tm = TRANS_RE.match(line)
        if tm and not books[idx]["freelancer_translation"]:
            books[idx]["freelancer_translation"] = tm.group(1).strip()

    # assemble final records
    records = []
    for b in books:
        scores = b.get("scores", {})
        ai = scores.get("ai", 0)
        ai_relevance = "high" if ai >= 7 else ("medium" if ai >= 4 else "low")
        legal = _legal(b["track"], b["title"])
        slug = re.sub(r"[^a-z0-9]+", "-", b["title"].lower()).strip("-")
        records.append({
            "slug": slug,
            "title": b["title"],
            "author": b["author"],
            "track": b["track"],
            "track_label": b["track_label"],
            "priority": b.get("priority", "REFERENCE"),
            "scores": scores,
            "legitimate_source": legal["legitimate_source"],
            "public_domain": legal["public_domain"],
            "summary_status": "complete",
            "ai_relevance": ai_relevance,
            "best_parts": b["best_parts"],
            "warnings": b["warnings"],
            "freelancer_translation": b["freelancer_translation"],
        })
    return records


def main() -> None:
    records = parse_digest()
    out = {
        "generated_from": str(DIGEST.relative_to(ROOT)),
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "count": len(records),
        "books": records,
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(records)} books -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
