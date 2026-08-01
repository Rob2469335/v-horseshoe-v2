"""RV Finder package — discovers used RVs under a budget and thoroughly analyzes
every listing to surface the best deal.

Layered package (infrastructure → domain → service):
- parsers.py   source adapters (PPL, web search) + HTTP + extraction helpers
- knowledge.py static RV domain data (types, makes, weak spots, life-ease spec)
- analysis.py  pure domain logic (fair value, condition, engine/mpg/solar/
               livability/life-ease scoring, Deal Score, negotiation tips)
- geo.py       distance-aware filtering (Nominatim geocoding + haversine + cache)
- llm.py       optional LLM deep-dive prompt + provider calls
- service.py   find_best_rv_deals orchestration (discovery → analysis → ranking)
- models.py    RVListing value object + wire serialization

Public API: `find_best_rv_deals(...)` (async) and `aclose()` (client shutdown).
"""
from .parsers import aclose
from .service import find_best_rv_deals

__all__ = ["find_best_rv_deals", "aclose"]
