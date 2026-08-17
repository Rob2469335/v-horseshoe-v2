"""RV Finder orchestration — the service layer.

Discovers candidates via the registered parser adapters, dedupes, analyzes every
listing (pure domain), ranks by Deal Score, surfaces the best overall deal and
the best motorhome, and runs an optional LLM deep-dive. Returns a plain dict
ready for the API layer to hand to FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from .analysis import _build_analysis, _is_motorhome_like
from .llm import _llm_deep_dive
from .models import RVListing, serialize_listing
from .parsers import DISCOVERY_PARSERS

logger = logging.getLogger(__name__)


def _normalize_type_filter(rv_type: str):
    """Build a predicate for the user's RV-type filter.

    Common aliases ("motorhome", "class b", "camper van", ...) map to any
    motorhome class; "class b/c" (and friends) map to Class B OR Class C only —
    never Class A; otherwise the term is classified to a concrete class.
    Returns None when the term is meaningless (no filter applied).
    """
    from .parsers import _classify_rv_type

    t = (rv_type or "").strip().lower()
    motorhome_aliases = {
        "motorhome",
        "motor home",
        "motor coach",
        "motorized",
        "self-propelled",
        "motorized rv",
        "class a/b/c",
        "class a/b",
        "van",
        "camper van",
        "sprinter",
        "rv",
        "camper",
        "auto",
        "engine",
    }
    # Class B + Class C only (excludes Class A) — the two-people-living-in-it combo.
    bc_aliases = {
        "class b/c",
        "class b or c",
        "class b and c",
        "b/c",
        "bc",
        "class b c",
        "b or c",
        "van c",
        "b and c",
    }
    if t in motorhome_aliases:
        return lambda l: "Motorhome" in l.rv_type
    if t in bc_aliases:
        return lambda l: l.rv_type in ("Class B Motorhome", "Class C Motorhome")
    want = _classify_rv_type(t)
    if want == "unknown":
        return None
    return lambda l: l.rv_type == want


def _build_summary(
    ranked: list[RVListing],
    best: RVListing | None,
    budget: int,
    elapsed: float,
    location: str = "",
    radius_miles: int = 0,
) -> str:
    n = len(ranked)
    if n == 0:
        msg = f"No used RVs under ${budget:,} were found on the reachable sources right now."
        if radius_miles > 0:
            msg += f" Nothing within {radius_miles} mi of {location.strip() or 'your location'} matched."
        msg += " Try widening the RV type or radius, or run again — inventory changes daily."
        return msg
    excellent = sum(
        1
        for l in ranked
        if (l.analysis.get("score") or {}).get("verdict") == "Excellent Deal"
    )
    good = sum(
        1
        for l in ranked
        if (l.analysis.get("score") or {}).get("verdict") == "Good Deal"
    )
    risky = sum(
        1
        for l in ranked
        if (l.analysis.get("score") or {}).get("verdict") == "High Risk"
    )
    avg_price = int(sum(l.price for l in ranked) / n) if n else 0
    lines = [
        f"Analyzed {n} used RVs under ${budget:,} (avg asking ${avg_price:,}) in {elapsed:.1f}s. "
        f"Verdicts: {excellent} excellent, {good} good, {risky} high-risk.",
    ]
    if radius_miles > 0 and location.strip():
        known = [l for l in ranked if l.distance_miles is not None]
        unknown = [l for l in ranked if l.distance_miles is None]
        if known:
            closest = min(known, key=lambda l: l.distance_miles or 1e18)
            lines.append(
                f"All shown are within {radius_miles} mi of {location.strip()} "
                f"(closest: {closest.year} {closest.make} {closest.model} at "
                f"{int(closest.distance_miles or 0)} mi)."
            )
        if unknown:
            lines.append(
                f"{len(unknown)} listing{'s' if len(unknown) > 1 else ''} had no usable "
                "location and are shown at the bottom — confirm distance before visiting."
            )
    if best:
        b = best.analysis.get("score") or {}
        dist = (
            f" at {int(best.distance_miles)} mi"
            if best.distance_miles is not None
            else ""
        )
        lines.append(
            f"Best deal: {best.year} {best.make} {best.model} ({best.rv_type}){dist} at "
            f"${int(best.price):,} — {b.get('verdict', '')} with a Deal Score of "
            f"{b.get('score', 'n/a')}/100. {best.analysis.get('negotiation_tip', '')}"
        )
    else:
        lines.append(
            "No unambiguously great deal emerged — treat anything high-risk as a pass until inspected."
        )
    return " ".join(lines)


async def find_best_rv_deals(
    budget: int = 30000,
    rv_type: str = "all",
    max_results: int = 40,
    deep_dive: int = 5,
    use_ppl: bool = True,
    use_web: bool = True,
    location: str = "",
    radius_miles: int = 0,
) -> dict[str, Any]:
    budget = int(budget) if budget else 30000
    started = time.time()
    raw: list[RVListing] = []

    from .geo import DEFAULT_LOCATION, DEFAULT_RADIUS_MILES

    location = (location or "").strip() or DEFAULT_LOCATION
    radius = int(radius_miles) if radius_miles else DEFAULT_RADIUS_MILES

    tasks = []
    if use_ppl:
        tasks.append(
            DISCOVERY_PARSERS["ppl"](
                budget, rv_type, max_results=max(1, int(max_results * 0.6))
            )
        )
    if use_web:
        tasks.append(
            DISCOVERY_PARSERS["web"](
                budget, rv_type, max_results=max(1, int(max_results * 0.5))
            )
        )
    # Craigslist is the most reliable real-marketplace source (private sellers,
    # no dealer markup) and is fetched with a real browser crawl.
    tasks.append(
        DISCOVERY_PARSERS["craigslist"](
            budget, rv_type, max_results=max(1, int(max_results * 0.5))
        )
    )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            logger.warning("discovery source failed: %r", r)
        else:
            raw.extend(r)

    # Dedupe by URL, prefer the richer (PPL) copy.
    by_url: dict[str, RVListing] = {}
    for lst in raw:
        if not lst.url:
            continue
        key = lst.url.rstrip("/").lower()
        existing = by_url.get(key)
        if existing is None or len(lst.description) > len(existing.description):
            by_url[key] = lst
    listings = list(by_url.values())

    # Secondary dedupe: Craigslist reposts the same unit under different hash
    # URLs — collapse listings that share (normalized title, price).
    title_key: dict[tuple, RVListing] = {}
    kept: dict[str, RVListing] = {}
    for lst in listings:
        norm = re.sub(r"[^a-z0-9]+", "", (lst.title or "").lower())
        if not norm or lst.price <= 0:
            kept[lst.url or lst.title] = lst
            continue
        key = (norm[:40], round(lst.price))
        existing = title_key.get(key)
        if existing is None or len(lst.description) > len(existing.description):
            title_key[key] = lst
    for lst in title_key.values():
        kept[lst.url or lst.title] = lst
    listings = list(kept.values())

    # Keep only listings that carry a real price, skip junk/parts (<$1k).
    listings = [l for l in listings if l.price >= 1000]

    # Apply RV type filter (user-facing term maps back to a class).
    if rv_type and rv_type != "all" and rv_type != "unknown":
        pred = _normalize_type_filter(rv_type)
        if pred is not None:
            listings = [l for l in listings if pred(l)]

    # Distance filter: geocode the anchor once, then each unique listing city.
    anchor = None
    if radius > 0 and location.strip():
        from .geo import _load_cache, haversine_miles, resolve_lat_lng

        cache = _load_cache()
        anchor = await resolve_lat_lng(location, cache)
        if anchor is None:
            logger.warning(
                "RV finder: could not geocode user location %r; skipping radius filter",
                location,
            )
        else:
            cities: dict[str, tuple[float, float]] = {}
            for lst in listings:
                if not lst.location:
                    continue
                if lst.location not in cities:
                    cities[lst.location] = await resolve_lat_lng(
                        lst.location, cache
                    ) or (None, None)  # type: ignore[assignment]
                lat, lng = cities[lst.location]
                if lat and lng:
                    lst.distance_miles = haversine_miles(anchor[0], anchor[1], lat, lng)
            # Hard filter: drop confirmed out-of-range, keep unknown-distance but demote.
            listings = [
                l
                for l in listings
                if l.distance_miles is None or l.distance_miles <= radius
            ]
            listings.sort(key=lambda l: 1 if l.distance_miles is None else 0)
            in_range = sum(1 for l in listings if l.distance_miles is not None)
            logger.info(
                "RV finder: %d of %d listings within %d mi of %s",
                in_range,
                len(listings),
                radius,
                location,
            )

    # Thorough analysis for every listing.
    for lst in listings:
        try:
            _build_analysis(lst, budget)
        except Exception as e:
            logger.warning("analysis failed for %s: %s", lst.url, e)

    # Rank by score; confirmed-distance listings first, unknown-distance demoted.
    ranked = sorted(
        listings,
        key=lambda l: (
            1 if l.distance_miles is None else 0,
            -((l.analysis.get("score") or {}).get("score", -1)),
            l.distance_miles if l.distance_miles is not None else 0,
        ),
    )[:max_results]

    # Best deal: highest score with an Excellent/Good verdict, no critical red
    # flags, and a properly identified vehicle (year + make). Snippet-only leads
    # are capped below "Good Deal", so this always lands on a fully-analyzed unit.
    best = None
    best_motorhome = None
    for l in ranked:
        s = l.analysis.get("score") or {}
        if not (l.year and l.make):
            continue
        if s.get("verdict") not in ("Excellent Deal", "Good Deal") or s.get(
            "critical_red_flags"
        ):
            continue
        if best is None:
            best = l
        if best_motorhome is None and _is_motorhome_like(l):
            best_motorhome = l
        if best and best_motorhome:
            break
    # Fallback: if no fully-analyzed motorhome qualified, surface the best real
    # motorhome lead we found (Class B/C listings are scarce in structured data).
    # The title itself must claim motorhome (never aggregator boilerplate), and
    # structured listings are preferred over snippet-only leads.
    if best_motorhome is None:
        from .analysis import _title_motorhome
        from .parsers import _is_junk_title

        candidates = [
            l
            for l in ranked
            if l.year
            and l.make
            and _title_motorhome(l)
            and not _is_junk_title(l.title, l.model)
        ]
        candidates.sort(key=lambda l: 1 if l.attrs else 0, reverse=True)
        if candidates:
            best_motorhome = candidates[0]

    source_counts: dict[str, int] = {}
    for l in ranked:
        src = l.source.split(" (")[0]
        source_counts[src] = source_counts.get(src, 0) + 1

    deep_dive_text = ""
    if deep_dive and deep_dive > 0 and ranked:
        deep_dive_text = await _llm_deep_dive(ranked[: max(deep_dive, 5)], budget)

    summary = _build_summary(
        ranked, best, budget, time.time() - started, location, radius_miles
    )

    return {
        "ok": True,
        "budget": budget,
        "rv_type": rv_type,
        "location": (location or "").strip(),
        "radius_miles": radius_miles,
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_seconds": round(time.time() - started, 1),
        "source_counts": source_counts,
        "total_found": len(ranked),
        "listings": [serialize_listing(l) for l in ranked],
        "top_pick": serialize_listing(best) if best else None,
        "best_motorhome": serialize_listing(best_motorhome) if best_motorhome else None,
        "summary": summary,
        "deep_dive": deep_dive_text,
    }
