"""Diversity-aware curriculum selector.

The mined pool is dominated by 2 single-tool families (sandbox_repl, filesystem),
so a plain "next uncompleted item" run is ~90% no-choice tasks. This selects a
BALANCED, interleaved subset (caps per family, keeps every rare family, prefers
harder items) from what we already have.

Honest scope: balancing can only re-weight the pool — it cannot invent families
that are absent. To add real tool-CHOICE diversity, generate more multi-tool
families (`_symbol_loc_items`, `_git_items`, `_symbol_items`, and new generators).

Read-only over the pool. Writes its own subset file.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import run_curriculum as rc  # noqa: E402

OUT = _HERE / "curriculum" / "diverse.jsonl"


def family(item: dict) -> tuple:
    return tuple(sorted(item.get("target_tools") or [])) or ("?",)


def _dedupe(group: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for it in group:
        p = it.get("prompt", "")
        if p in seen:
            continue
        seen.add(p)
        out.append(it)
    return out


def select_diverse(
    items: list[dict], per_family: int = 150, prefer_hard: bool = True
) -> list[dict]:
    """Cap each family, drop duplicate prompts, prefer harder items, interleave.

    Interleaving (round-robin across families) means consecutive runs alternate
    tool families instead of grinding one family 1,000 times.
    """
    by_family: dict[tuple, list[dict]] = collections.defaultdict(list)
    for it in items:
        if it.get("split") != "train":
            continue
        by_family[family(it)].append(it)

    buckets: list[list[dict]] = []
    for fam, group in by_family.items():
        uniq = _dedupe(group)
        if prefer_hard:
            uniq.sort(key=lambda x: -(x.get("difficulty") or 0))
        buckets.append(uniq[:per_family])

    out: list[dict] = []
    while any(buckets):
        for b in buckets:
            if b:
                out.append(b.pop(0))
    return out


def family_distribution(items: list[dict]) -> dict:
    c = collections.Counter(family(it) for it in items)
    return {"|".join(k): v for k, v in c.most_common()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Select a balanced curriculum subset")
    ap.add_argument("--per-family", type=int, default=150)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-prefer-hard", action="store_true")
    args = ap.parse_args()

    pool = rc.load_items()
    chosen = select_diverse(pool, args.per_family, not args.no_prefer_hard)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for it in chosen:
            json.dump(it, fh, ensure_ascii=False)
            fh.write("\n")

    print("pool family distribution (before):")
    for k, v in family_distribution(pool).items():
        print(f"  {v:5d}  {k}")
    print(f"\nselected: {len(chosen)} items (per_family={args.per_family})")
    print("selected family distribution (after):")
    for k, v in family_distribution(chosen).items():
        print(f"  {v:5d}  {k}")
    print(f"\nwrote -> {out.relative_to(_HERE.parent)}")
    print("NOTE: wire into run_curriculum.load_items (or a --pool flag) post-run to run it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
