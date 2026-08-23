"""Distance-aware filtering for RV Finder.

Geocodes the user's location and each listing's "City, ST" to lat/lng using
OpenStreetMap Nominatim (no API key, ~1 req/s), computes great-circle miles,
and persists a JSON cache so daily runs only geocode new cities.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

DEFAULT_LOCATION = os.environ.get("RV_FINDER_LOCATION", "Roosevelt, NY 11575")
DEFAULT_RADIUS_MILES = int(os.environ.get("RV_FINDER_RADIUS_MILES", "50"))

_CACHE_PATH = Path(
    os.environ.get("RV_FINDER_GEO_CACHE", "data/rv_finder_geo_cache.jsonl")
)

_GEO_CLIENT: "httpx.AsyncClient | None" = None
_GEO_LOCK = asyncio.Lock()
_last_request_at = 0.0
_MIN_GEO_INTERVAL = 1.1  # Nominatim politely asks for >=1s between requests


def _get_geo_client():
    global _GEO_CLIENT
    if _GEO_CLIENT is None or _GEO_CLIENT.is_closed:
        import httpx

        _GEO_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=8.0, read=20.0, write=10.0, pool=12.0),
            headers={
                "User-Agent": "v-horseshoe-rv-finder/1.0 (personal daily-deal search)",
                "Accept": "application/json",
            },
            follow_redirects=True,
        )
    return _GEO_CLIENT


async def aclose_geo():
    global _GEO_CLIENT
    if _GEO_CLIENT is not None:
        try:
            await _GEO_CLIENT.aclose()
        except Exception:
            pass
        _GEO_CLIENT = None


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in miles between two lat/lng points."""
    r = 3958.7613  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def _load_cache() -> dict[str, list[float]]:
    try:
        if _CACHE_PATH.exists():
            cache = {}
            for line in _CACHE_PATH.read_text(encoding="utf-8").splitlines():
                if not line.strip(): continue
                data = json.loads(line)
                cache[data["key"]] = data["val"]
            return cache
    except Exception as e:
        logger.warning("geo cache load failed: %s", e)
    return {}


def _save_cache_entry(key: str, val: list[float]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CACHE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "val": val}) + "\n")
    except Exception as e:
        logger.warning("geo cache save failed: %s", e)


def _normalize(loc: str) -> str:
    """Collapse a listing location string to a stable, cache-friendly key."""
    s = (loc or "").strip().lower()
    s = s.replace("  ", " ")
    # "City, ST" and bare zip stay as-is; drop "near"/"in" prefixes.
    for prefix in ("in ", "near "):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    # If it contains a 5-digit zip, prefer just the zip (most precise, stable).
    m = __import__("re").search(r"\b(\d{5})\b", s)
    if m:
        return m.group(1)
    return s.strip(", .")[:60]


async def resolve_lat_lng(
    loc: str, cache: dict[str, list[float]] | None = None
) -> tuple[float, float] | None:
    """Resolve a location string (zip or "City, ST") to (lat, lng).

    Uses the shared cache first, then Nominatim, rate-limited to ~1 req/s.
    Returns None when the location is unparseable or geocoding fails.
    """
    key = _normalize(loc)
    if not key:
        return None
    cache = cache if cache is not None else _load_cache()
    hit = cache.get(key)
    if hit:
        return float(hit[0]), float(hit[1])

    global _last_request_at
    async with _GEO_LOCK:
        now = time.monotonic()
        wait = _MIN_GEO_INTERVAL - (now - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()
        try:
            r = await _get_geo_client().get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": loc.strip()[:100],
                    "format": "jsonv2",
                    "limit": 1,
                    "addressdetails": 0,
                },
            )
            if r.status_code == 200:
                results = r.json()
                if results and isinstance(results[0], dict):
                    lat = float(results[0].get("lat", 0) or 0)
                    lng = float(results[0].get("lon", 0) or 0)
                    if lat and lng:
                        cache[key] = [lat, lng]
                        _save_cache_entry(key, [lat, lng])
                        return lat, lng
        except Exception as e:
            logger.warning("geocode failed for %r: %s", loc, e)
    return None
