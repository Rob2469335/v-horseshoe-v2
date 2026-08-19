"""Pure domain logic — every listing gets a thorough, deterministic analysis.

No HTTP, no LLM, no scraping here. Analyzers consume the RVListing value object
and the static knowledge tables and return plain dicts. The deal score, fair
value, engine / mpg / solar / livability / life-ease / weak-spot readouts and
the negotiation tip are all unit-testable in isolation.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .knowledge import (
    BASE_NEW_PRICE,
    CRITICAL_RED_FLAGS,
    EXTREME_UNDERPRICE_RATIO,
    KNOWN_MOTORHOME_MODELS,
    KNOWN_WEAK_SPOTS,
    LIFE_EASE_FEATURES,
    MPG_BY_TYPE,
    POSITIVE_SIGNALS,
    SCAM_RISK_PATTERNS,
)
from .models import RVListing
from .parsers import _attr_int, _attr_str


# Title-level motorhome signals strong enough for the headline pick.
_MOTORHOME_TITLE_RE = re.compile(
    r"\bmotorhome\b|\bmotor home\b|\bmotor coach\b|\bclass [abc]\b|\bcamper van\b|"
    r"\bcampervan\b|\bsprinter\b|\bdiesel pusher\b|\brv chassis\b|\bvan\s+camper\b|"
    r"\bvan\s+conversion\b|\btravato\b"
)

# Brands that build (almost) only self-propelled motorhomes — their models are
# unambiguous. Big makes (Coachmen, Forest River, Winnebago...) build trailers
# too, so a model token alone is never trusted from them.
_MOTORHOME_ONLY_MAKES = (
    "roadtrek",
    "pleasure-way",
    "pleasure way",
    "sportsmobile",
    "leisure travel",
    "ltv",
    "lazy daze",
)


def _title_motorhome(lst: RVListing) -> bool:
    """Does the listing's own title/model claim to be self-propelled?

    Used to vet motorhome leads: the headline 'best motorhome' pick must carry a
    motorhome signal in the listing's own title, never in aggregator boilerplate.
    An already-classified non-motorhome type (e.g. "Fifth Wheel") always wins —
    a trailer model name like "Aurora" must not override it.
    """
    if "Motorhome" in lst.rv_type:
        return True
    if lst.rv_type and lst.rv_type != "unknown":
        return False
    blob = " ".join(filter(None, [lst.title, lst.model or ""])).lower()
    if _MOTORHOME_TITLE_RE.search(blob):
        return True
    # Motorhome-only brands are trustworthy when a real listing is present.
    make_key = (lst.make or "").lower()
    if any(m in make_key for m in _MOTORHOME_ONLY_MAKES):
        return bool(re.search(r"\b(19[89]\d|20[0-2]\d)\b", lst.title or ""))
    return False


def _is_motorhome_like(lst: RVListing) -> bool:
    """Heuristic: is this unit self-propelled (motorhome) even if rv_type is unknown?"""
    if "Motorhome" in lst.rv_type:
        return True
    if lst.rv_type != "unknown":
        return False
    # Title/attrs carry the strongest signal ("2016 Winnebago View 24M").
    blob = " ".join(
        filter(
            None,
            [
                lst.title,
                lst.model or "",
                " ".join(v for v in lst.attrs.values() for v in v[:2]),
            ],
        )
    ).lower()
    if re.search(
        r"\bmotorhome\b|\bmotor coach\b|\bclass [abc]\b|\bcamper van\b|\bsprinter\b|\bdiesel pusher\b|\brv chassis\b|\bvan\b",
        blob,
    ):
        return True
    # Description-only motorhome words are only trusted when unambiguous
    # (a bare "van" in prose is too weak and matches aggregator boilerplate).
    desc = (lst.description or "")[:600].lower()
    if re.search(
        r"\bmotorhome\b|\bmotor coach\b|\bclass [abc]\b|\bcamper van\b|\bsprinter\b|\bdiesel pusher\b",
        desc,
    ):
        return True
    model_key = (lst.model or "").lower().split()[0] if lst.model else ""
    return model_key in KNOWN_MOTORHOME_MODELS


def _analyze_engine(lst: RVListing) -> dict[str, str]:
    """Engine / powertrain assessment. Motorhomes read the structured attrs;
    towables explain what truck they'll need instead."""
    a = lst.attrs
    mfg = _attr_str(a, "Engine Manufacturer")
    hp = _attr_str(a, "Engine HP")
    size = _attr_str(a, "Engine Size")
    trans = _attr_str(a, "Transmission")
    chassis = _attr_str(a, "Chassis")
    weight = _attr_int(a, "Weight")

    if "Motorhome" in lst.rv_type or _is_motorhome_like(lst):
        bits = []
        if mfg:
            bits.append(mfg)
        if size:
            bits.append(size)
        if hp:
            bits.append(f"{hp}hp")
        if trans:
            bits.append(trans)
        desc = " ".join(bits) if bits else "Engine details not disclosed"
        fuel = (
            "diesel"
            if re.search(
                r"diesel|caterpillar|cat |cummins|detroit", f"{mfg} {size}".lower()
            )
            else "gas"
        )
        return {
            "type": "self-propelled motorhome",
            "engine": desc or "not disclosed",
            "fuel": fuel,
            "chassis": chassis or "not disclosed",
            "note": "",
        }

    # Towable — no engine.
    hitch = "Class 5 / gooseneck" if lst.rv_type == "Fifth Wheel" else "Class 3-4 hitch"
    if weight:
        tv = (
            "3/4-ton or 1-ton truck"
            if weight >= 8000
            else (
                "half-ton truck or full-size SUV"
                if weight >= 5000
                else "mid-size SUV / van"
            )
        )
        note = f"Needs a {tv}; unit weighs ~{weight:,} lbs GVWR. Budget the tow vehicle into the total cost."
    else:
        note = "Needs a capable tow vehicle; verify its payload/GCWR before committing."
    return {
        "type": "towable (no engine)",
        "engine": "n/a — you supply the tow vehicle",
        "fuel": "n/a",
        "chassis": f"towing with {hitch}",
        "note": note,
    }


def _analyze_mpg(lst: RVListing) -> dict[str, str]:
    """Fuel economy estimate by RV class + powertrain; towing guidance for towables."""
    a = lst.attrs
    if _is_motorhome_like(lst):
        rv_type = lst.rv_type if lst.rv_type != "unknown" else "Class C Motorhome"
    else:
        rv_type = lst.rv_type
    lo, hi, kind = MPG_BY_TYPE.get(rv_type, (0.0, 0.0, "tow"))
    engine = _attr_str(a, "Engine Manufacturer")
    if kind == "tow":
        if lst.sleeps and lst.size_ft:
            return {
                "mpg_estimate": "n/a",
                "detail": (
                    f"No engine — fuel economy is your tow vehicle's. A {lst.size_ft}ft unit for {lst.sleeps} "
                    "usually tows with a half-ton or larger; expect the tow vehicle's MPG to drop roughly "
                    "30-40% while towing (e.g., 18 mpg → ~11-12 mpg)."
                ),
            }
        return {
            "mpg_estimate": "n/a",
            "detail": (
                "Towable — MPG depends on the tow vehicle. Plan for a 30-40% MPG hit while towing; "
                "a half-ton truck/SUV is the usual minimum."
            ),
        }
    fuel = (
        "diesel"
        if re.search(r"diesel|caterpillar|cat |cummins|detroit", engine.lower())
        else "gas"
    )
    if fuel == "diesel":
        lo, hi = (
            lo - 0.5,
            hi - 1.0,
        )  # diesels are slightly thirstier per gallon but cheaper per mile
    return {
        "mpg_estimate": f"~{lo:.0f}-{hi:.0f} mpg",
        "detail": (
            f"{fuel} {rv_type}. Expect roughly {lo:.0f}-{hi:.0f} mpg in mixed use; "
            "worse with full water tanks, a toad, or headwinds. Factor fuel into trip budgets."
        ),
    }


def _analyze_solar(lst: RVListing) -> dict[str, str]:
    """Solar power hookup complexity: what's already there + what adding solar takes."""
    a = lst.attrs
    solar = _attr_str(a, "Solar Panel")
    elec = _attr_str(a, "Electrical") or "30amp"
    gen = _attr_str(a, "Generator Manufacturer")
    gen_w = _attr_int(a, "Generator Watts")
    fridge_12v = _attr_str(a, "Refrigerator 12V")

    has_solar = bool(solar) and solar.strip().lower() not in ("false", "no", "0")
    if has_solar:
        return {
            "has_solar": True,
            "complexity": "Low",
            "detail": (
                f"Already solar-equipped ({elec} electrical). Hookup is plug-and-play if a charge controller "
                "and battery bank are in place — just top off batteries from panels. Consider adding "
                "a lithium bank + inverter later if boondocking."
            ),
        }
    base = "Not equipped with solar"
    complexity = "Moderate"
    if elec == "50amp":
        complexity = "Low-Moderate"
    extras = []
    if gen:
        extras.append(f"{gen} generator ({gen_w}W)" if gen_w else f"{gen} generator")
    if fridge_12v and fridge_12v.strip().lower() in ("true", "yes", "1"):
        extras.append("12V fridge (ideal for solar, no propane needed)")
    if extras:
        base += "; has " + ", ".join(extras)
    return {
        "has_solar": False,
        "complexity": complexity,
        "detail": (
            f"{base}. Solar hookup complexity is {complexity.lower()}: roof space is the main constraint. "
            "A 200-400W starter kit (panels + MPPT controller + wiring) is a do-it-yourself weekend job "
            f"on this {elec} system; expect ~$800-$2,000 installed. "
            "A 50amp system gives headroom to expand later."
        ),
    }


def _all_text(lst: RVListing) -> str:
    return " ".join(
        filter(
            None,
            [
                lst.title,
                lst.description[:1500],
                " ".join(v for v in lst.attrs.values() for v in v[:3]),
            ],
        )
    )


def _feature_present(lst: RVListing, feat: dict[str, Any]) -> bool:
    """Evaluate one pure-data LIFE_EASE_FEATURES entry against a listing."""
    if feat.get("solar") and (lst.analysis.get("solar") or {}).get("has_solar"):
        return True
    if feat.get("low_miles"):
        if _is_motorhome_like(lst):
            return bool(lst.mileage) and 0 < lst.mileage < 60000
        return True
    if feat.get("attr_true"):
        if _attr_str(lst.attrs, feat["attr_true"]).strip().lower() in (
            "true",
            "yes",
            "1",
        ):
            return True
    elif feat.get("attr") and _attr_str(lst.attrs, feat["attr"]).strip():
        return True
    ge = feat.get("attr_ge")
    if ge and (_attr_int(lst.attrs, ge[0]) or 0) >= ge[1]:
        return True
    contains = feat.get("attr_contains")
    if contains and contains[1].lower() in _attr_str(lst.attrs, contains[0]).lower():
        return True
    rx = feat.get("regex")
    if rx and re.search(rx, _all_text(lst), re.I):
        return True
    return False


def _analyze_life_ease(lst: RVListing) -> dict[str, Any]:
    """Checklist of the life-easing features owners actually want for 2 people,
    scored as a 0-100 'life ease' number and listed as present/missing."""
    items = []
    present_n = 0
    for feat in LIFE_EASE_FEATURES:
        present = _feature_present(lst, feat)
        if present:
            present_n += 1
        items.append(
            {
                "key": feat["key"],
                "label": feat["label"],
                "why": feat["why"],
                "present": present,
            }
        )
    score = round(100.0 * present_n / len(items)) if items else 0

    if lst.rv_type in (
        "Class A Motorhome",
        "Class B Motorhome",
        "Class C Motorhome",
    ) or _is_motorhome_like(lst):
        note = "Motorhome: this checklist is the 'make life easy for 2' spec sheet owners care about."
    else:
        note = "Towable: add a capable tow vehicle + brake controller; the checklist still applies to the trailer itself."
    return {
        "score": score,
        "present_count": present_n,
        "total_count": len(items),
        "checklist": items,
        "note": note,
    }


def _analyze_weak_spots(lst: RVListing) -> dict[str, Any]:
    """Known brand weak spots + how to check them at inspection."""
    make_key = (lst.make or "").lower()
    if not make_key:
        return {
            "make": lst.make or "unknown",
            "weak_spots": [],
            "summary": "Brand unknown — have a licensed RV inspector go over it.",
        }
    spots = KNOWN_WEAK_SPOTS.get(make_key)
    if not spots:
        return {
            "make": lst.make,
            "weak_spots": [],
            "summary": f"No strong community consensus on {lst.make} weak spots; still insist on a certified inspection.",
        }
    return {
        "make": lst.make,
        "weak_spots": [{"issue": i, "check": c} for i, c in spots],
        "summary": f"{lst.make}'s known trouble areas — check each before committing.",
    }


def _analyze_livability(lst: RVListing) -> dict[str, Any]:
    """Does this unit actually work for 2 people living in it, with a shower?"""
    a = lst.attrs
    bath = _attr_str(a, "Bath").lower()
    sleeps = lst.sleeps or _attr_int(a, "Sleeps") or 0
    queens = _attr_int(a, "Queen Beds") or 0
    doubles = _attr_int(a, "Double Beds") or 0
    bunks = _attr_int(a, "Bunk Beds") or 0
    size = lst.size_ft or _attr_int(a, "Size") or 0
    toy_hauler = _attr_str(a, "Toy Hauler")

    # No structured data and no textual hints? Say so instead of guessing.
    has_data = bool(a) or bool(sleeps) or bool(size) or bool(lst.description)
    desc_hint = (
        re.search(
            r"\b(shower|bath|queen bed|double bed|bunk|dry bath)\b",
            lst.description.lower(),
        )
        if lst.description
        else None
    )
    if not has_data and not desc_hint:
        return {
            "verdict": "Unknown (no spec data)",
            "detail": "Snippet-only listing — confirm bed, shower, and floorplan on the source page.",
            "has_shower": None,
            "bed_for_two": None,
            "sleeps": 0,
            "size_ft": 0,
            "toy_hauler": False,
        }

    has_shower = "shower" in bath or bool(
        re.search(r"\bshower\b|\bdry bath\b", lst.description.lower())
    )
    has_bed = queens + doubles >= 1 or bool(
        re.search(r"\bqueen bed\b|\bdouble bed\b", lst.description.lower())
    )
    space_ok = size >= 22 or (size == 0)  # 0 = unknown, don't penalize

    if has_shower and has_bed and space_ok:
        verdict = "Good for 2"
        detail = f"Dedicated shower, bed for two, {size}ft of living space"
    elif has_shower and has_bed:
        verdict = "OK for 2"
        detail = f"Has shower and a bed for two, but compact at {size}ft"
    elif has_bed:
        verdict = "Tight for 2"
        detail = "No real shower" + (f" ({size}ft)" if size else "")
    else:
        verdict = "Not ideal for 2"
        detail = "No proper bed or shower for two-person living"

    if bunks:
        detail += f", sleeps up to {sleeps} w/ bunks" if sleeps else ""
    elif sleeps:
        detail += f", sleeps {sleeps}"
    return {
        "verdict": verdict,
        "detail": detail,
        "has_shower": has_shower,
        "bed_for_two": has_bed,
        "sleeps": sleeps,
        "size_ft": size,
        "toy_hauler": bool(toy_hauler),
    }


def _estimate_fair_value(
    year: int, rv_type: str, mileage: int | None = None
) -> dict[str, float]:
    """Depreciation curve: ~28% off in year 1, ~7%/yr after, 15% floor.
    Mileage penalizes motorhomes > 50k and towables > 30k."""
    base = BASE_NEW_PRICE.get(rv_type, BASE_NEW_PRICE["unknown"])
    if not year:
        return {"low": 0, "high": 0, "fair": 0, "basis": base}
    now_year = time.localtime().tm_year
    age = max(0, now_year - year)
    factor = 0.72 * (0.93 ** max(0, age - 1))
    factor = max(0.15, min(1.0, factor))
    fair = base * factor

    if mileage:
        threshold = 50000 if "Motorhome" in rv_type or rv_type == "unknown" else 30000
        if mileage > threshold:
            fair *= 0.9

    return {
        "low": round(fair * 0.85),
        "high": round(fair * 1.15),
        "fair": round(fair),
        "basis": base,
    }


def _analyze_condition(text: str) -> dict[str, Any]:
    lowered = (text or "").lower()
    red_flags = [kw for kw in CRITICAL_RED_FLAGS if kw in lowered]
    positives = [kw for kw in POSITIVE_SIGNALS if kw in lowered]
    # collapse duplicate semantic hits (e.g. "leak" also matches "roof leak")
    return {"red_flags": red_flags, "positives": positives}


def _analyze_scam_risk(lst: RVListing) -> dict[str, Any]:
    """Deterministic scam / legitimacy risk for a private-party listing.

    2026 research (BBB, RV Reports' private-seller scam patterns, community
    reports) shows private-seller RV scams concentrate in a few machine-detectable
    text signals: shipping/escrow/out-of-state stories, deposit + urgency pressure,
    non-reversible payment channels, selling-on-behalf claims, and refusal of
    photos/inspection. Detected signals must CAP the deal score below "Good Deal"
    so a scammy bargain can never rank as an excellent deal.
    """
    blob = " ".join(
        filter(
            None,
            [
                lst.title,
                lst.description[:1500],
                " ".join(v for v in lst.attrs.values() for v in v[:2]),
            ],
        )
    ).lower()
    hits: list[str] = []
    for rx, label in SCAM_RISK_PATTERNS:
        if re.search(rx, blob):
            if label not in hits:
                hits.append(label)
    # A clean private-party listing carries no penalty signal, but the buyer
    # should still verify by inspection — surfaced as a note, never as a scored
    # scam signal (which would cap every legit Craigslist deal below "Good Deal").
    private_party = (lst.source or "").lower() in ("craigslist (private)", "facebook")
    return {
        "signals": hits,
        "risk": len(hits),
        "verify_by_inspection": bool(hits or private_party),
    }


def _score_listing(lst: RVListing) -> dict[str, Any]:
    fair = lst.analysis.get("fair_value") or {"low": 0, "high": 0, "fair": 0}
    fair_est = fair["fair"]

    # 1) Price value (0-40): how far under fair-market estimate are we.
    if fair_est > 0 and lst.price > 0:
        ratio = fair_est / lst.price  # >1 means priced below estimate
        price_value = min(40.0, max(0.0, 40 * (ratio / 1.3)))
    else:
        ratio = 0.0
        price_value = 15.0  # no baseline → neutral

    # 2) Condition (0-30).
    reds = lst.analysis.get("red_flags") or []
    pos = lst.analysis.get("positives") or []
    condition = 20.0
    condition -= min(20.0, len(reds) * 8.0)
    condition += min(10.0, len(pos) * 2.0)
    condition = max(0.0, min(30.0, condition))

    # 3) Features / upgrades (0-15).
    features = 0.0
    premium = [
        "solar",
        "lithium",
        "inverter",
        "leveling jacks",
        "new roof",
        "new tires",
        "generator",
        "slide",
    ]
    for kw in premium:
        if kw in (pos or []):
            features += 2.5
    if (lst.analysis.get("solar") or {}).get("has_solar"):
        features += 2.5  # already solar-equipped is a real premium
    features = min(15.0, features)

    # 4) Freshness / age (0-10) — no date data → neutral 5.
    freshness = 5.0

    # 5) Source quality (0-5): consignment & private = best deals.
    src = lst.source.lower()
    if "consignment" in src:
        source_q = 5.0
    elif "facebook" in src or "craigslist" in src:
        source_q = 4.0
    elif "dealer" in src:
        source_q = 2.0
    else:
        source_q = 3.0

    raw = price_value + condition + features + freshness + source_q

    # Low-information penalty: snippet-only leads (no structured attrs) can't be
    # verified, so they're capped below "Good Deal" no matter how cheap.
    low_info = not lst.attrs
    if low_info:
        raw = min(raw, 55.0)

    # Critical red flags cap the verdict.
    critical = len(
        [
            r
            for r in reds
            if r
            in (
                "water damage",
                "water intrusion",
                "roof leak",
                "mold",
                "frame damage",
                "frame rot",
                "salvage",
                "rebuilt title",
                "flood",
                "fire damage",
                "blown",
                "does not run",
                "junk",
            )
        ]
    )
    if critical:
        raw = min(raw, 39.0)

    # Scam-signal cap: detected private-seller scam signals must never rank as a
    # clean deal (deterministic guard over heuristic text — see _analyze_scam_risk).
    scam = lst.analysis.get("scam_risk") or {}
    n_scam = len(scam.get("signals") or [])
    if n_scam:
        # Each real scam signal is a heavy penalty; cap below "Good Deal" so even
        # a cheap bargain can't dress up as excellent.
        raw = min(raw, 30.0 + n_scam)
        scam_capped = True
    else:
        scam_capped = False

    # Extreme-underpricing caveat: a price far below estimated fair value is
    # either a genuine steal or a scam/parts-unit. Cap below "Excellent Deal"
    # (not "Good Deal") and add a verify-before-commit note rather than praising it.
    underpriced_capped = False
    if (
        fair_est > 0
        and lst.price > 0
        and (lst.price / fair_est) <= EXTREME_UNDERPRICE_RATIO
    ):
        raw = min(raw, 74.0)
        underpriced_capped = True

    score = round(max(0.0, min(100.0, raw)), 1)

    if score >= 75:
        verdict = "Excellent Deal"
    elif score >= 60:
        verdict = "Good Deal"
    elif score >= 45:
        verdict = "Fair Price"
    elif score >= 30:
        verdict = "Overpriced"
    else:
        verdict = "High Risk"

    if critical:
        verdict = "High Risk"
    if scam_capped:
        verdict = "High Risk"
    if underpriced_capped and critical == 0 and n_scam == 0 and score >= 60:
        verdict = (
            "Good Deal"  # genuine-looking deeply-underpriced steals cap at Good Deal
        )

    return {
        "score": score,
        "price_value": round(price_value, 1),
        "condition": round(condition, 1),
        "features": round(features, 1),
        "freshness": freshness,
        "source_quality": source_q,
        "ratio": round(ratio, 2),
        "verdict": verdict,
        "critical_red_flags": critical,
    }


def _build_analysis(lst: RVListing, budget: int) -> dict[str, Any]:
    fair = _estimate_fair_value(lst.year, lst.rv_type, lst.mileage)
    cond = _analyze_condition(lst.description or lst.title)
    lst.analysis.update(
        {
            "fair_value": fair,
            "fair_value_range": f"${fair['low']:,}-${fair['high']:,}"
            if fair["low"]
            else "n/a",
            "red_flags": cond["red_flags"],
            "positives": cond["positives"],
            "engine": _analyze_engine(lst),
            "mpg": _analyze_mpg(lst),
            "solar": _analyze_solar(lst),
            "weak_spots": _analyze_weak_spots(lst),
            "livability": _analyze_livability(lst),
            "scam_risk": _analyze_scam_risk(lst),
        }
    )
    lst.analysis["life_ease"] = _analyze_life_ease(lst)
    score = _score_listing(lst)
    lst.analysis["score"] = score

    pros = list(cond["positives"][:5])
    cons = list(cond["red_flags"][:5])
    if lst.mileage:
        pros.append(f"Documented mileage ({lst.mileage:,} mi)")
    if fair["fair"] and lst.price:
        diff = fair["fair"] - lst.price
        if diff > 0:
            pros.append(f"~${diff:,} below estimated fair value")
        else:
            cons.append(f"~${abs(diff):,} above estimated fair value")

    liv = lst.analysis.get("livability") or {}
    if liv.get("verdict"):
        if liv["verdict"] in ("Good for 2", "OK for 2"):
            pros.append(f"Living for 2: {liv['verdict'].lower()} ({liv['detail']})")
        else:
            cons.append(f"Living for 2: {liv['verdict'].lower()} ({liv['detail']})")

    solar = lst.analysis.get("solar") or {}
    if solar.get("has_solar"):
        pros.append("Already solar-equipped (lowest hookup effort)")
    else:
        cons.append(
            f"No solar installed — adding it is {solar.get('complexity', 'moderate').lower()} complexity"
        )
    cons = cons[:7]

    # Scam warnings are appended AFTER the truncation carve-out so a real
    # scam-signal is never trimmed away by the cosmetic-cons cap.
    scam = lst.analysis.get("scam_risk") or {}
    scam_signals = scam.get("signals") or []
    if scam_signals:
        cons.append(f"Scam risk ({len(scam_signals)}): {', '.join(scam_signals[:3])}")
    elif scam.get("verify_by_inspection"):
        cons.append("Private seller — verify title, photos, and condition in person")

    engine = lst.analysis.get("engine") or {}
    if engine.get("note"):
        cons.append(engine["note"])

    negotiation_tip = ""
    if fair["fair"] and lst.price:
        low, high = fair["low"], fair["high"]
        if lst.price > high:
            negotiation_tip = f"Listed above fair range (${low:,}-${high:,}); start negotiation near ${low:,}."
        elif lst.price < low:
            if lst.price / fair["fair"] <= EXTREME_UNDERPRICE_RATIO:
                negotiation_tip = (
                    "Priced far below fair range — could be a genuine steal or a "
                    "scam/parts-unit. Verify title, photos, and a live inspection "
                    "before committing."
                )
            else:
                negotiation_tip = "Already priced below fair range — act fast and verify condition thoroughly."
        else:
            negotiation_tip = f"Within fair range (${low:,}-${high:,}); a small discount may be achievable."

    lst.analysis.update(
        {
            "pros": pros,
            "cons": cons,
            "verdict": score["verdict"],
            "negotiation_tip": negotiation_tip,
            "reasoning": _reasoning_text(lst, fair, score),
        }
    )
    return lst.analysis


def _reasoning_text(lst: RVListing, fair: dict, score: dict) -> str:
    parts = []
    if fair["fair"] and lst.price:
        pct = int(round((lst.price / fair["fair"]) * 100))
        parts.append(f"listed at ~{pct}% of estimated fair value")
    if lst.analysis.get("red_flags"):
        parts.append(
            f"{len(lst.analysis['red_flags'])} red flag(s): {', '.join(lst.analysis['red_flags'][:4])}"
        )
    if lst.analysis.get("positives"):
        parts.append(f"positives: {', '.join(lst.analysis['positives'][:4])}")
    if lst.mileage:
        parts.append(f"mileage {lst.mileage:,}")
    liv = lst.analysis.get("livability") or {}
    if liv.get("verdict"):
        parts.append(f"2-person livability: {liv['verdict'].lower()}")
    mpg = lst.analysis.get("mpg") or {}
    if mpg.get("mpg_estimate") and mpg["mpg_estimate"] != "n/a":
        parts.append(f"fuel {mpg['mpg_estimate']}")
    return "; ".join(parts) if parts else "insufficient listing detail"
