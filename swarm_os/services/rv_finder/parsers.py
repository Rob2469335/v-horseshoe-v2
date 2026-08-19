"""Source adapters — discovery parsers for each listing provider.

Every parser exposes the same shape: an async `discover(budget, rv_type,
max_results) -> list[RVListing]`. Parsers are infrastructure: they own HTTP,
HTML, and snippet text munging, but never scoring/analysis (see analysis.py).

Registered in `DISCOVERY_PARSERS` so the service can enable/disable sources
and future sources (RV Trader direct, Craigslist, etc.) just register here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from urllib.parse import urlparse

import httpx

from .knowledge import (
    BROWSER_HEADERS,
    PPL_BASE,
    PPL_INVENTORY,
    RV_MAKES,
    RV_TYPE_TERMS,
)
from .models import RVListing

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# HTTP transport (lazy pooled client, shared by every parser)
# --------------------------------------------------------------------------
_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_http() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        # SSRF defense: redirects are re-checked on every hop (the swarm's own
        # loopback services — Qdrant/llama.cpp/backend — must never be reached
        # by a fetched listing URL or a redirect off it). Mirrors the web_fetch
        # guard in swarm_os/lib/mcp/web_search.py.
        from swarm_os.lib.mcp.web_search import _ssrf_redirect_hook

        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=8.0, read=45.0, write=20.0, pool=12.0),
            headers=BROWSER_HEADERS,
            follow_redirects=True,
            event_hooks={"response": [_ssrf_redirect_hook]},
        )
    return _HTTP_CLIENT


def _assert_public_url(url: str) -> None:
    """Pre-flight SSRF check: refuse to fetch a URL pointing at loopback/private/
    link-local/reserved addresses (or hostnames resolving to them)."""
    from swarm_os.lib.mcp.web_search import _ssrf_check

    blocked = _ssrf_check(url)
    if blocked:
        raise ValueError(f"SSRF blocked: {blocked}")


async def _fetch_text(url: str) -> str | None:
    try:
        _assert_public_url(url)
        r = await _get_http().get(url)
        if r.status_code < 400:
            return r.text
    except Exception as e:
        logger.warning("fetch failed %s: %s", url, e)
    return None


async def aclose():
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        try:
            await _HTTP_CLIENT.aclose()
        except Exception:
            pass
        _HTTP_CLIENT = None
    try:
        from .geo import aclose_geo

        await aclose_geo()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Text extraction helpers
# --------------------------------------------------------------------------
def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def parse_ppl_attributes(html: str) -> dict[str, list]:
    """Extract the structured `tenant~rv-*` attributes embedded in a PPL detail page.

    Returns a dict of human attribute name -> list of string values (e.g.
    {"Bath": ["Shower"], "Electrical": ["50amp"], "Queen Beds": ["1"]}).
    """
    attrs: dict[str, list] = {}
    decoder = json.JSONDecoder()
    for m in re.finditer(r'\{"attributeFQN":"tenant~rv-[^"]+"', html or ""):
        try:
            obj, _ = decoder.raw_decode(html[m.start() : m.start() + 1200])
        except Exception:
            continue
        detail = obj.get("attributeDetail") or {}
        name = detail.get("name") or obj.get("attributeFQN", "").replace(
            "tenant~rv-", ""
        )
        vals = []
        for v in (obj.get("values") or [])[:8]:
            if isinstance(v, dict):
                if "stringValue" in v:
                    vals.append(v["stringValue"])
                elif "value" in v:
                    vals.append(str(v["value"]))
        if vals:
            attrs.setdefault(name, []).extend(
                v for v in vals if v and v not in ("False", "false")
            )
    return attrs


def _extract_price(text: str) -> float | None:
    m = re.search(r"\$\s?([\d,]{3,7}(?:\.\d{2})?)", text or "")
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.search(r"\b([\d]{4,7})\b", (text or "")[:40])
    if m and 3000 <= int(m.group(1)) <= 300000:
        return float(m.group(1))
    return None


def _extract_year(text: str) -> int | None:
    m = re.search(r"\b(19[89]\d|20[0-2]\d)\b", text or "")
    if m:
        y = int(m.group(1))
        if 1980 <= y <= time.localtime().tm_year + 1:
            return y
    return None


def _extract_mileage(text: str) -> int | None:
    m = re.search(r"\b(\d{1,3}(?:,\d{3}){1,2})\s*(?:mi|miles)\b", text or "", re.I)
    if m:
        return int(m.group(1).replace(",", ""))
    m = re.search(r"\b([\d]{4,6})\s*(?:mi|miles|km)\b", text or "", re.I)
    if m:
        v = int(m.group(1))
        if 500 <= v <= 400000:
            return v
    return None


_US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
    "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)


def _extract_location(text: str) -> str:
    # Require a real US state abbreviation so feature text like "Speakers, TV" won't match.
    def _fmt(g1: str, g2: str) -> str:
        return f"{g1.strip().title()}, {g2.upper()}" if g2.upper() in _US_STATES else ""

    m = re.search(
        r"\b(?:in|near)\s+([A-Z][A-Za-z .]{2,30}?)\s*[,.]?\s*([A-Z]{2})\b", text or ""
    )
    if m:
        loc = _fmt(m.group(1), m.group(2))
        if loc:
            return loc
    m = re.search(r"\b([A-Z][A-Za-z .]{2,30}),\s*([A-Z]{2})\b", text or "")
    if m:
        return _fmt(m.group(1), m.group(2))
    return ""


def _classify_rv_type(*texts: str) -> str:
    joined = " ".join(t.lower() for t in texts if t)
    for rv_type, terms in RV_TYPE_TERMS.items():
        if any(term in joined for term in terms):
            return rv_type
    return "unknown"


def _attr_int(attrs: dict[str, list], name: str) -> int | None:
    vals = attrs.get(name) or []
    for v in vals:
        m = re.search(r"\d+", str(v))
        if m:
            return int(m.group(0))
    return None


def _attr_str(attrs: dict[str, list], name: str) -> str:
    vals = attrs.get(name) or []
    return " ".join(str(v) for v in vals).strip()


def _extract_make_model(title: str, url: str = "") -> tuple[str, str]:
    text = f"{title} {url}".lower()
    for make in RV_MAKES:
        if make in text:
            idx = text.find(make)
            rest = re.sub(r"[^a-z0-9 .\-]", " ", text[idx + len(make) :]).strip()
            # drop craigslist/url noise tokens before taking the model
            rest = re.sub(r"\b(craigslist|www|http|org|com|view|d)\b", " ", rest)
            # take up to 4 tokens as the model, stop at digits that look like lengths/ids
            tokens = [t for t in rest.split() if t][:4]
            model = " ".join(tokens)
            return make.title(), model
    # fallback: strip year + price-ish tokens from title
    cleaned = re.sub(r"^\s*(20\d\d)\s*", "", title or "")
    cleaned = re.sub(r"\$\s?[\d,]+.*$", "", cleaned).strip()
    cleaned = re.sub(r"\b(craigslist|www|http|org|com|view|d)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return "", cleaned[:40]


# --------------------------------------------------------------------------
# PPL Motorhomes parser
# --------------------------------------------------------------------------
async def _discover_ppl(
    budget: int, rv_type: str = "all", max_results: int = 40
) -> list[RVListing]:
    """Scrape the PPL consignment inventory page filtered to `budget`."""
    html = await _fetch_text(PPL_INVENTORY.format(budget=int(budget)))
    if not html:
        logger.warning("PPL inventory fetch failed.")
        return []

    items = re.findall(
        r'data-mz-product="rv-(\d+)".*?mz-productlisting-title.*?href="(/used-rvs-for-sale/[^"]+)".*?mz-price[^>]*>[^<]*\$([\d,]+)',
        html,
        re.DOTALL,
    )
    listings: list[RVListing] = []
    seen: set[str] = set()
    for stock, path, price in items:
        url = PPL_BASE + path
        if url in seen:
            continue
        seen.add(url)
        price_v = float(price.replace(",", ""))
        if not (1000 <= price_v <= budget):
            continue
        # URL shape: /used-rvs-for-sale/{type}/{year}-{make}-{model}_rv-{id}
        m = re.search(r"/used-rvs-for-sale/([^/]+)/(\d{4})-([^_]+)_rv-", path)
        year = int(m.group(2)) if m else 0
        type_slug = m.group(1) if m else ""
        slug = m.group(3) if m else ""
        title = slug.replace("-", " ").title()
        rv_type = _classify_rv_type(type_slug.replace("-", " "), title)
        make, model = _extract_make_model(title)
        listings.append(
            RVListing(
                source="PPL Motorhomes (consignment)",
                stock_id=f"PPL-{stock}",
                title=f"{year} {title}".strip(),
                year=year,
                make=make,
                model=model,
                rv_type=rv_type,
                price=price_v,
                url=url,
            )
        )
        if len(listings) >= max_results:
            break

    # Fetch detail pages for full descriptions (bounded concurrency).
    sem = asyncio.Semaphore(6)
    logger.info("PPL found %d candidates; fetching detail pages...", len(listings))

    async def enrich(lst: RVListing):
        if lst.price > budget:
            return
        async with sem:
            detail = await _fetch_text(lst.url)
        if not detail:
            return
        try:
            m = re.search(r"/used-rvs-for-sale/([^/]+)/", lst.url)
            type_slug = m.group(1) if m else ""
            attrs = parse_ppl_attributes(detail)
            lst.attrs = attrs
            text = _clean_text(detail)
            lst.description = text[:3000]
            lst.mileage = _extract_mileage(text) or _attr_int(attrs, "Mileage")
            if not lst.location:
                lst.location = _extract_location(text) or _attr_str(
                    attrs, "RV Location"
                )
            if not lst.year:
                lst.year = _attr_int(attrs, "Year") or 0
            if not lst.rv_type or lst.rv_type == "unknown":
                lst.rv_type = _classify_rv_type(
                    _attr_str(attrs, "RV Type") or type_slug.replace("-", " "),
                    lst.title,
                )
            if attrs.get("Brand"):
                lst.make = lst.make or " ".join(attrs["Brand"]).title()
            if attrs.get("Model"):
                lst.model = lst.model or " ".join(attrs["Model"]).title()
            if attrs.get("Size"):
                lst.size_ft = _attr_int(attrs, "Size")
            if attrs.get("Sleeps"):
                lst.sleeps = _attr_int(attrs, "Sleeps")
        except Exception as e:
            logger.warning("PPL detail enrich failed for %s: %s", lst.url, e)

    await asyncio.gather(*(enrich(l) for l in listings), return_exceptions=True)
    return listings


# --------------------------------------------------------------------------
# Web-search parser (RV Trader / RVUSA / Facebook / etc. via snippets)
# --------------------------------------------------------------------------
def _build_queries(budget: int, rv_type: str, max_results: int) -> list[str]:
    term = ""
    if rv_type and rv_type != "all" and rv_type != "unknown":
        term = rv_type.lower()
    core = f"used {term} rv" if term else "used rv"
    return [
        f"{core} under {budget} for sale",
        f"{core} under {budget} site:rvtrader.com",
        f"{core} under {budget} site:rvusa.com",
        f"{core} for sale under {budget} facebook marketplace",
        f"used {term or 'motorhome camper'} under {budget} price",
        f"{core} cheap under {budget} classifieds",
    ]


def _parse_snippet(title_txt: str, body: str, url: str) -> dict:
    """Parse a search result into a listing candidate.

    `title_txt` is the result's own title (the listing's heading); `body` is the
    search engine's snippet text. Make/model/type are derived from the TITLE
    only — the snippet body is page boilerplate (nav links, filter chrome,
    similar-vehicle blocks) and routinely names other RVs.
    """
    full = f"{title_txt} {body}"
    price = _extract_price(full)
    year = _extract_year(full)
    location = _extract_location(full)
    title = _clean_web_title(title_txt)
    make, model = _extract_make_model(title)
    rv_type = _classify_rv_type(title)
    return {
        "price": price,
        "year": year,
        "location": location,
        "make": make,
        "model": model,
        "rv_type": rv_type,
        "title": title,
    }


_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")

_BOILERPLATE = re.compile(
    r"\b(under\s*\$\s?[\d,]+k?\s*for\s*sale|for\s*sale|classifieds?|listing|"
    r"local\s*pickup|used\s*rvs?\s*under|new\s*[&]?\s*used\s*rvs?\b|call\s*seller\b|"
    r"call\s*dealer\b|call\s*for\s*price\b|in\s*or\s*near\b|near\s+me\b|results\b|"
    r"www\.\S+|\.com|rvusa|rvtrader|craigslist|rvs?\s*by\s*owner\b|west\s+valley\b|"
    r"available\s+in\b)\b",
    re.I,
)

# Titles that are aggregator / search-result pages wearing a listing costume.
_JUNK_TITLE_RES = [
    re.compile(r"\b(?:used|new)\s+(?:[a-z0-9]+\s+){0,5}rvs?\s+under\b", re.I),
    re.compile(r"\b(?:used|new)\s+rvs?\s+(?:near|in|by|under)\b", re.I),
    re.compile(r"\brvs?\s+under\s*\$\s?[\d,]+\b", re.I),
    re.compile(r"\brv\s*trader\b|\brv\s*usa\b", re.I),
    re.compile(
        r"^\s*(?!\d{4}\b)(?:used|new)?\s*motorhomes?\s+(?:under|for\s*sale|near)\b",
        re.I,
    ),
    re.compile(r"\bcall\s+(?:dealer|for\s+price|seller)\b", re.I),
    re.compile(r"\bnear\s+me\b", re.I),
    re.compile(
        r"\b(?:used|new)\s+(?:[a-z0-9]+\s+){0,3}class\s+[abc]\s+(?:rvs?|motorhomes?)\b",
        re.I,
    ),
    re.compile(r"\b(?:search|results?)\b", re.I),  # "Search Results" furniture
    re.compile(r"\bpage\s+\d+\b", re.I),  # "Page 3" furniture
    re.compile(r"(?:\b\d{1,3}\b\s+){4,}"),  # tabular run like "12 24 48 96"
]

# Model tokens that are page furniture, not an actual model name.
_JUNK_MODELS = {
    "motor",
    "coach",
    "rv",
    "rvs",
    "camper",
    "van",
    "class",
    "dealer",
    "motorhome",
    "motorhomes",
    "sale",
    "price",
    "units",
    "inventory",
    "listing",
    "vehicles",
    "special",
    "stock",
    "miles",
    "campervan",
}


def _is_junk_title(title: str, model: str = "") -> bool:
    """True if a title is an aggregator/search page rather than a real listing."""
    t = title or ""
    if not t.strip():
        return True
    if any(rx.search(t) for rx in _JUNK_TITLE_RES):
        return True
    if (model or "").strip().lower() in _JUNK_MODELS:
        return True
    return False


def _clean_web_title(text: str) -> str:
    cleaned = _PHONE_RE.sub(" ", text or "")
    cleaned = _BOILERPLATE.sub(" ", cleaned)
    # drop leading list/ordinal markers like "32." or "- " from snippet scrape
    cleaned = re.sub(r"^\s*\d{1,3}\.\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|").strip()
    if len(cleaned) > 90:
        cleaned = cleaned[:90].rsplit(" ", 1)[0]
    return cleaned[:90]


def _looks_like_real_listing(url: str, parsed: dict, snippet_text: str = "") -> bool:
    """Skip youtube / search-result / aggregator / auction noise from web discovery."""
    if "youtube.com" in url or "youtu.be" in url or "google.com" in url:
        return False
    # eBay category/search pages are not listings.
    if "ebay.com" in url and ("/sch/" in url or "/b/" in url):
        return False
    # Require a recognized RV make — pure aggregator pages rarely name one.
    if not parsed["make"]:
        return False
    title = parsed["title"]
    if len(title) < 10:
        return False
    if _is_junk_title(title, parsed["model"]):
        return False
    if re.search(r"\brv\s*travel\s*world\b|\bclassifieds?\b", title, re.I):
        return False
    # "for sale" is common on real listings; only reject it as category
    # furniture when it reads like a heading ("... for sale" / "... for sale by owner").
    if re.search(
        r"\bfor\s*sale\b\s*(?:$|by\s+owner\b|near\s+me\b|under\s*\$?)", title, re.I
    ):
        return False
    if re.search(r"\beBay\b|\bbid\b|\bauction\b|\bwatchers?\b", title, re.I):
        return False
    if re.search(
        r"\bnew\s*[&]?\s*used\s*rvs?\b|\bcall\s*seller\b|\bin\s*or\s*near\b|\brvusa\b|\brvtrader\b|\blistings?\s+in\b",
        title,
        re.I,
    ):
        return False
    # A real listing title should carry a year or the make-derived model token.
    if not re.search(r"\b(19[89]\d|20[0-2]\d)\b", title):
        if not parsed["model"]:
            return False
    # A motorhome call needs a motorhome signal in the actual text (not only a URL slug).
    if parsed["rv_type"] != "unknown" and "Motorhome" in parsed["rv_type"]:
        if not re.search(
            r"\bmotorhome\b|\bclass [abc]\b|\bmotor coach\b|\bcamper van\b|\bsprinter\b|\bvan\b",
            snippet_text,
            re.I,
        ):
            parsed["rv_type"] = "unknown"
    return True


async def _discover_web(
    budget: int, rv_type: str = "all", max_results: int = 40
) -> list[RVListing]:
    from swarm_os.lib.mcp.web_search import web_search_handler

    listings: list[RVListing] = []
    seen: set[str] = set()
    queries = _build_queries(budget, rv_type, max_results)

    for q in queries:
        try:
            res = await web_search_handler({"query": q, "max_results": 5})
        except Exception as e:
            logger.warning("web search failed (%s): %s", q, e)
            continue
        if not res.get("ok"):
            continue
        for item in res.get("results") or []:
            url = item.get("url", "")
            title_txt = item.get("title", "")
            body = item.get("snippet", "")
            snippet = f"{title_txt} {body}"
            if not url or url in seen:
                continue
            seen.add(url)
            parsed = _parse_snippet(title_txt, body, url)
            if not _looks_like_real_listing(url, parsed, snippet):
                continue
            if parsed["price"] is not None and not (1000 <= parsed["price"] <= budget):
                continue
            host = urlparse(url).netloc.replace("www.", "").split(".")[0] or "web"
            listings.append(
                RVListing(
                    source=f"{host.title()} (web)",
                    title=parsed["title"],
                    year=parsed["year"] or 0,
                    make=parsed["make"],
                    model=parsed["model"],
                    rv_type=parsed["rv_type"],
                    price=parsed["price"] or 0.0,
                    url=url,
                    location=parsed["location"],
                    description=snippet[:3000],
                )
            )
            if len(listings) >= max_results:
                break
        if len(listings) >= max_results:
            break
    return listings


# --------------------------------------------------------------------------
# Craigslist parser (real marketplace — private sellers, no dealer markup).
# Uses the Crawl4AI browser fetch so the JS-rendered listing grid is read.
# --------------------------------------------------------------------------
def _build_craigslist_queries(budget: int, rv_type: str) -> list[str]:
    """Craigslist search URLs for the budget + (optionally) the RV type.

    For a live-in RV for 2 people, the self-propelled classes matter most, so
    the default ('all') and motorhome-family filters ALSO run a dedicated
    motorhome/campervan search — otherwise the grid is dominated by cheap
    trailers and the Class B/C vans you actually want get buried.
    """
    area = "longisland"  # user's home area; extendable to a radius later
    base = f"https://{area}.craigslist.org/search/rvs?max_price={int(budget)}"
    urls = [base]
    t = (rv_type or "").lower()
    want_motorhome = (
        rv_type == "all"
        or rv_type == "unknown"
        or "motorhome" in t
        or t
        in (
            "class a/b/c",
            "class a/b",
            "class b/c",
            "class b or c",
            "b/c",
            "van",
            "camper van",
            "sprinter",
            "rv",
            "camper",
        )
    )
    if want_motorhome:
        # Campervan = Class B (van conversions); motorhome = Class A/C + B.
        urls.append(base + "&auto_make_model=campervan")
        urls.append(base + "&auto_make_model=motorhome")
    else:
        term = {
            "class b motorhome": "campervan",
            "class c motorhome": "motorhome",
            "travel trailer": "trailer",
            "fifth wheel": "fifth",
            "toy hauler": "toy",
            "truck camper": "camper",
        }.get(t, t)
        if term and term != "all":
            urls.append(base + f"&auto_make_model={term}")
    return urls


def _parse_craigslist_content(content: str, budget: int) -> list[RVListing]:
    """Parse the Crawl4AI markdown of a Craigslist /search/rvs page into listings.

    Listing shape (verified live): `[TITLE](https://www.craigslist.org/view/d/{area}-{slug}/{hash})`
    followed on the same text run by `| {MM/DD}{Area} | ${price}`. Only /view/d/
    links are real listings — /search/, images, and nav are skipped.
    """
    from .analysis import _is_motorhome_like  # noqa: F401  (type helpers below)

    listings: list[RVListing] = []
    # Real listings are links whose URL is /view/d/{area}-{slug}/{hash}; the
    # price follows as "| {MM/DD}{Area} | $N". Image thumbnails precede the real
    # link in markdown ([![alt](img.jpg)](view-url)), so read the title as the
    # LAST "[...]" before "(view-url)" and strip any inner image markdown.
    for m in re.finditer(
        r"\(https://www\.craigslist\.org/view/d/([^)]+)\)", content or ""
    ):
        slug = m.group(1)
        url = f"https://www.craigslist.org/view/d/{slug}"
        # The final URL segment is the stable Craigslist post ID — the dedup key
        # (a full-URL compare breaks when the same post's area/slug tokens change).
        post_id = slug.rsplit("/", 1)[-1].strip() or ""
        pre = content[max(0, m.start() - 200) : m.start()]
        lb = pre.rfind("[")
        lc = pre.rfind("]")
        title = pre[lb + 1 : lc] if lb != -1 and lc > lb else ""
        # Drop any "](image-url)" that rode along inside the alt text.
        title = re.sub(r"\]\(https?://\S+\)?", "", title)
        title = re.sub(r"[_!]+", "", title).strip()
        title = re.sub(r"\s+\d+$", "", title).strip()
        title = re.sub(r"\s*https?://\S+", "", title).strip()
        title = re.sub(r"\s+", " ", title).strip()
        # Crawl4AI truncates long image-alt titles ("...5th WHEEL TR"). The URL
        # slug carries the full readable title — use it when it's longer.
        slug_title = re.sub(r"[-_]+", " ", slug.rsplit("/", 1)[0])
        slug_title = re.sub(r"^\s*\S+\s+", "", slug_title)  # drop area word
        if len(slug_title) > len(title):
            title = re.sub(r"\s+\d+$", "", slug_title).strip().title()
        if len(title) < 6:
            continue
        # Price follows within the next ~160 chars as "$N,NNN"
        tail = content[m.end() : m.end() + 160]
        price_m = re.search(r"\$\s?([\d,]{3,7}(?:\.\d{2})?)", tail)
        if not price_m:
            continue
        price = float(price_m.group(1).replace(",", ""))
        if not (1000 <= price <= budget):
            continue
        # Location: from the "| {MM/DD}{Area} |" segment right before the price.
        loc_m = re.search(r"\|\s*\d{1,2}/\d{1,2}\s*([A-Za-z .-]+?)\s*\|\s*\$", tail)
        location = loc_m.group(1).strip().title() if loc_m else ""
        # Year / make / model from the title via existing helpers. The Craigslist
        # title is complete (make + model), so the URL slug is NOT fed in — its
        # area/hash tokens would pollute the model extraction.
        year = _extract_year(title) or 0
        make, model = _extract_make_model(title, "")
        rv_type = _classify_rv_type(title)
        if location:
            location = f"{location}, NY"
        # Mark as a verified real listing (not a snippet-only lead) with the
        # data we parsed from the page, so it scores honestly.
        attrs: dict[str, list] = {"_verified": ["craigslist"]}
        if year:
            attrs["Year"] = [str(year)]
        if rv_type != "unknown":
            attrs["RV Type"] = [rv_type]
        listings.append(
            RVListing(
                source="Craigslist (private)",
                stock_id=post_id,
                title=title,
                year=year,
                make=make,
                model=model,
                rv_type=rv_type,
                price=price,
                url=url,
                location=location,
                attrs=attrs,
                description=f"{title}. Private-sale listing on Craigslist (Long Island) — inspect thoroughly; verify title before paying.",
            )
        )
    return listings


async def _discover_craigslist(
    budget: int, rv_type: str = "all", max_results: int = 40
) -> list[RVListing]:
    """Discover used-RV listings from Craigslist via the Crawl4AI browser fetch."""
    from swarm_os.lib.mcp.web_search import web_fetch_handler

    listings: list[RVListing] = []
    seen: set[str] = set()
    for url in _build_craigslist_queries(budget, rv_type):
        try:
            res = await web_fetch_handler({"url": url, "max_chars": 20000})
        except Exception as e:
            logger.warning("craigslist fetch failed (%s): %s", url, e)
            continue
        if not res.get("ok"):
            continue
        for lst in _parse_craigslist_content(res.get("content") or "", budget):
            # Dedup by the stable Craigslist post ID (a full-URL compare would
            # let the same post resurface when its area/slug tokens differ).
            dedup_key = lst.stock_id or lst.url
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            listings.append(lst)
            if len(listings) >= max_results:
                break
        if len(listings) >= max_results:
            break
    return listings


# --------------------------------------------------------------------------
# Parser registry
# --------------------------------------------------------------------------
DISCOVERY_PARSERS = {
    "ppl": _discover_ppl,
    "web": _discover_web,
    "craigslist": _discover_craigslist,
}
