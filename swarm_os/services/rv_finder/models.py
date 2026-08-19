"""RV Finder domain model — the typed value object every layer shares.

The dataclass is the internal, trusted representation of a discovered listing.
Serialization to plain dicts happens here so callers (API, renderers) never
touch the internal shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RVListing:
    """A single discovered used-RV listing across any source."""

    source: str = ""
    stock_id: str = ""
    title: str = ""
    year: int = 0
    make: str = ""
    model: str = ""
    rv_type: str = "unknown"
    price: float = 0.0
    url: str = ""
    location: str = ""
    description: str = ""
    mileage: int | None = None
    size_ft: int | None = None
    sleeps: int | None = None
    distance_miles: float | None = None
    attrs: dict[str, list] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)


def serialize_listing(lst: RVListing | None) -> dict[str, Any] | None:
    """Project a listing to the wire shape consumed by the API."""
    if lst is None:
        return None
    return {
        "source": lst.source,
        "stock_id": lst.stock_id,
        "title": lst.title,
        "year": lst.year,
        "make": lst.make,
        "model": lst.model,
        "rv_type": lst.rv_type,
        "price": round(lst.price, 2),
        "url": lst.url,
        "location": lst.location,
        "mileage": lst.mileage,
        "size_ft": lst.size_ft,
        "sleeps": lst.sleeps,
        "distance_miles": round(lst.distance_miles, 1)
        if lst.distance_miles is not None
        else None,
        "description": (lst.description or "")[:2000],
        "analysis": {
            "fair_value_range": lst.analysis.get("fair_value_range"),
            "fair_value": lst.analysis.get("fair_value"),
            "score": lst.analysis.get("score"),
            "pros": lst.analysis.get("pros"),
            "cons": lst.analysis.get("cons"),
            "red_flags": lst.analysis.get("red_flags"),
            "verdict": lst.analysis.get("verdict"),
            "negotiation_tip": lst.analysis.get("negotiation_tip"),
            "reasoning": lst.analysis.get("reasoning"),
            "engine": lst.analysis.get("engine"),
            "mpg": lst.analysis.get("mpg"),
            "solar": lst.analysis.get("solar"),
            "weak_spots": lst.analysis.get("weak_spots"),
            "livability": lst.analysis.get("livability"),
            "life_ease": lst.analysis.get("life_ease"),
            "scam_risk": lst.analysis.get("scam_risk"),
        },
    }
