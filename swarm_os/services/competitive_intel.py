# swarm_os/services/competitive_intel.py
"""Competitive Intelligence Monitor — the money-making product of the Command
Center stack (per-report CI briefs, weekly digests).

Architecture (hard boundaries, per the SOTA research):

    Competitor Registry
            |
            v
    Collection Layer          -- deterministic ONLY, no LLM
     - webpage snapshots (homepage/pricing/product/changelog/careers)
     - RSS/feed ingestion (reuses news_digest)
            |
            v
    Change Events             -- factual delta, reproducible
            |
            v
    Intelligence Layer        -- classification + significance + dedup + so-what
     - classification is deterministic (rule-based)
     - "so what" is the ONLY LLM seam, behind IntelligenceSynthesizer
            |
            v
    Curated Digest            -- capped at ~10-15 items
     - what_changed / why_it_matters / recommended_action
            |
     +------+-------+------+
     v      v        v      v
   Email Telegram Slack  Command Center

The detector NEVER consults an LLM: it establishes the factual delta first.
The model interprets that delta, never decides whether a change happened.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

from swarm_os.lib.mcp.web_search import web_fetch_handler

log = logging.getLogger(__name__)

_DATA_DIR = Path(os.getenv("SWARM_INTEL_DATA_DIR", "data/intel"))
_REGISTRY_FILE = _DATA_DIR / "registry.json"
_SNAPSHOTS_DIR = _DATA_DIR / "snapshots"
_CHANGES_FILE = _DATA_DIR / "changes.jsonl"
_DIGESTS_DIR = _DATA_DIR / "digests"
_DELIVERY_FILE = _DATA_DIR / "delivery.jsonl"

_LOCK = threading.RLock()


def _paths():
    """Resolve data paths dynamically so tests (and env changes) can point the
    module at a temp dir via SWARM_INTEL_DATA_DIR without reimport."""
    d = Path(os.getenv("SWARM_INTEL_DATA_DIR", "data/intel"))
    return {
        "data_dir": d,
        "registry": d / "registry.json",
        "snapshots": d / "snapshots",
        "changes": d / "changes.jsonl",
        "digests": d / "digests",
        "delivery": d / "delivery.jsonl",
    }


# Monitored target kinds (what a competitor gets monitored on).
TARGET_KINDS = ("homepage", "pricing", "product", "changelog", "careers", "feed")

# Tier labels — top_3 gets full monitoring; tier_2 gets major changes only.
TIERS = ("top_3", "tier_2")

# Significance scoring (deterministic).
_SIG_WEIGHTS = {
    "pricing": 3.0,
    "changelog": 2.5,
    "product": 2.5,
    "homepage": 2.0,
    "careers": 1.5,
    "feed": 1.5,
}

# Keyword families for classifying a change's nature.
_CLASS_KEYWORDS: dict[str, tuple[str, float]] = {
    "pricing": (
        r"\$\s?[\d,]+(?:\.\d+)?|\bprice\b|\bpricing\b|\bper month\b|\bper year\b|\bplan\b|\bfree trial\b|\bbilled\b",
        3.0,
    ),
    "product_feature": (
        r"\bfeature\b|\blaunch(?:ed|es|ing)?\b|\brelease(?:d|s|ing)?\b|\bnew\b|\bversion\b|\bv\d|changelog",
        2.5,
    ),
    "hiring": (
        r"\bhiring\b|\bcareers\b|\bjob(s| posting)?\b|\bwe'?re looking\b|\bjoin(?: us| our)?\b|\bopen role",
        1.5,
    ),
    "marketing_content": (
        r"\bblog\b|\bannounce(?:d|ment)?\b|\bwebinar\b|\bnewsletter\b|\bcase study\b|\bpress\b",
        1.0,
    ),
    "acquisition_partnership": (
        r"\bacquire(?:d|s)?\b|\bpartnership?\b|\bintegration with\b|\bfunding\b|\braised\b|\bround\b",
        2.5,
    ),
}

# Stopword-ish tokens for "did anything actually change" noise reduction.
_NOISE_TOKENS = {
    "cookie",
    "consent",
    "privacy",
    "subscribe",
    "subscribe to our",
    "we use cookies",
    "your privacy",
    "accept all",
    "manage cookies",
    "sign in",
    "log in",
    "sign up",
    "unsubscribe",
    "javascript",
    "enable javascript",
    "read more",
    "loading",
}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _ensure_dirs() -> None:
    p = _paths()
    p["data_dir"].mkdir(parents=True, exist_ok=True)
    p["snapshots"].mkdir(parents=True, exist_ok=True)
    p["digests"].mkdir(parents=True, exist_ok=True)


def _load_registry() -> list[dict]:
    p = _paths()
    if not p["registry"].exists():
        return []
    try:
        return json.loads(p["registry"].read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("intel registry load failed: %s", exc)
        return []


def _save_registry(reg: list[dict]) -> None:
    p = _paths()
    _ensure_dirs()
    tmp = p["registry"].with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    os.replace(tmp, p["registry"])


def _append_changes(events: list[dict]) -> None:
    p = _paths()
    _ensure_dirs()
    # Append directly to the target UNDER THE LOCK. The old tmp+os.replace
    # pattern opened a FRESH .tmp in append mode then atomically replaced the
    # target — so every write silently TRUNCATED all history before that call
    # (the change/delivery trail only ever kept the most recent batch). Readings
    # tolerate a trailing partial line, so a direct append stays safe.
    with _LOCK:
        with p["changes"].open("a", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")


def _load_changes(limit: int = 500) -> list[dict]:
    p = _paths()
    if not p["changes"].exists():
        return []
    out: list[dict] = []
    with p["changes"].open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out[-limit:]


def _snapshot_path(competitor_id: str, kind: str) -> Path:
    return _paths()["snapshots"] / f"{competitor_id}_{kind}.json"


def _load_snapshot(competitor_id: str, kind: str) -> dict | None:
    p = _snapshot_path(competitor_id, kind)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("intel snapshot load failed %s: %s", p, exc)
        return None


def _save_snapshot(competitor_id: str, kind: str, snap: dict) -> None:
    _ensure_dirs()
    p = _snapshot_path(competitor_id, kind)
    # Unique-per-write temp name: a static `.tmp` meant two overlapping writes
    # (the scan_all fan-out in the daemon) reused the SAME temp path — one
    # truncate+write could interleave the other's JSON, and os.replace then
    # shipped a corrupt snapshot that _load_snapshot treats as baseline.
    tmp = p.with_suffix(f".tmp.{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _append_delivery(record: dict) -> None:
    p = _paths()
    _ensure_dirs()
    # Same history-preserving append as _append_changes; the old tmp+replace
    # also truncated the delivery trail on every write. The JSON write is
    # lock-guarded (single writer) like every other persistence point.
    with _LOCK:
        with p["delivery"].open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")


def _load_deliveries(limit: int = 100) -> list[dict]:
    p = _paths()
    if not p["delivery"].exists():
        return []
    out: list[dict] = []
    with p["delivery"].open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out[-limit:]


# ---------------------------------------------------------------------------
# Registry CRUD
# ---------------------------------------------------------------------------
def add_competitor(
    name: str,
    url: str,
    tier: str = "tier_2",
    targets: list[str] | None = None,
) -> dict:
    """Register a competitor. `url` is the homepage; targets are inferred kinds
    plus any explicit ones. Returns the created competitor dict."""
    name = (name or "").strip()
    url = (url or "").strip()
    if not name or not url:
        return {"ok": False, "error": "name and url are required"}
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "url must be http(s)"}
    if tier not in TIERS:
        return {"ok": False, "error": f"tier must be one of {TIERS}"}
    explicit = [t for t in (targets or []) if t in TARGET_KINDS]
    with _LOCK:
        reg = _load_registry()
        for c in reg:
            if c.get("url") == url or c.get("name", "").lower() == name.lower():
                return {"ok": False, "error": "competitor already registered"}
        comp = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "url": url,
            "tier": tier,
            "targets": sorted(set(explicit) | {_infer_targets(url)}),
            "created_at": _now(),
            "enabled": True,
        }
        reg.append(comp)
        _save_registry(reg)
    return {"ok": True, "competitor": comp}


def _infer_targets(url: str) -> str:
    """Best-guess primary target kind for a homepage URL."""
    return "homepage"


def list_competitors() -> list[dict]:
    with _LOCK:
        return _load_registry()


def remove_competitor(competitor_id: str) -> dict:
    with _LOCK:
        reg = _load_registry()
        nxt = [c for c in reg if c.get("id") != competitor_id]
        if len(nxt) == len(reg):
            return {"ok": False, "error": "competitor not found"}
        _save_registry(nxt)
    return {"ok": True}


def update_competitor(
    competitor_id: str,
    name: str | None = None,
    url: str | None = None,
    tier: str | None = None,
    targets: list[str] | None = None,
    enabled: bool | None = None,
) -> dict:
    with _LOCK:
        reg = _load_registry()
        for c in reg:
            if c.get("id") != competitor_id:
                continue
            if name is not None:
                c["name"] = name.strip()
            if url is not None:
                if not url.strip().lower().startswith(("http://", "https://")):
                    return {"ok": False, "error": "url must be http(s)"}
                c["url"] = url.strip()
            if tier is not None:
                if tier not in TIERS:
                    return {"ok": False, "error": f"tier must be one of {TIERS}"}
                c["tier"] = tier
            if targets is not None:
                c["targets"] = sorted(
                    set(t for t in targets if t in TARGET_KINDS)
                    | {_infer_targets(c["url"])}
                )
            if enabled is not None:
                c["enabled"] = bool(enabled)
            _save_registry(reg)
            return {"ok": True, "competitor": c}
    return {"ok": False, "error": "competitor not found"}


# ---------------------------------------------------------------------------
# Collection layer — deterministic fetch + normalize + diff
# ---------------------------------------------------------------------------
def _normalize_text(text: str) -> str:
    """Canonicalize page text so irrelevant churn (whitespace, nav, dynamic
    counters) doesn't register as a change."""
    t = re.sub(r"<[^>]+>", " ", text)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\b\d{1,3}(?:,\d{3})+\b", "<NUM>", t)  # normalize counters
    t = t.strip().lower()
    return t


def _meaningful_delta(old: str, new: str, kind: str) -> bool:
    """Deterministic check: is the new text meaningfully different from the
    stored snapshot, after noise filtering?"""
    if not old:
        return bool(_strip_noise(new))
    if not new:
        return False
    old_n = _normalize_text(_strip_noise(old))
    new_n = _normalize_text(_strip_noise(new))
    if old_n == new_n:
        return False
    old_tokens = _tokenize(old_n)
    new_tokens = _tokenize(new_n)
    if not new_tokens:
        return False
    added = new_tokens - old_tokens
    removed = old_tokens - new_tokens
    if not added and not removed:
        return False
    if kind == "feed":
        return bool(added)  # feeds are additive by nature
    # ratio of changed tokens must exceed a floor to count (visualping-style
    # noise: cookies, nav counters, timestamps)
    total = max(1, len(old_tokens | new_tokens))
    changed_ratio = (len(added) + len(removed)) / total
    return changed_ratio >= 0.05


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9#@$-]{3,}", text))


def _strip_noise(text: str) -> str:
    lines = [l for l in text.splitlines() if l.strip()]
    keep = []
    for l in lines:
        ll = l.lower()
        if any(n in ll for n in _NOISE_TOKENS):
            continue
        keep.append(l)
    return "\n".join(keep)


async def _fetch_target(url: str) -> tuple[bool, str, str]:
    """Fetch a URL and return (ok, content, title). Deterministic fetch path."""
    try:
        res = await web_fetch_handler({"url": url, "max_chars": 30000})
        if not res.get("ok"):
            return False, "", str(res.get("error", "fetch failed"))
        text = res.get("markdown") or res.get("text") or res.get("content") or ""
        title = res.get("title") or ""
        return bool(text.strip()), text, title
    except Exception as exc:
        return False, "", str(exc)


def _target_url(comp: dict, kind: str) -> str:
    """Resolve the URL for a given target kind of a competitor."""
    base = comp.get("url", "").rstrip("/")
    if kind == "homepage":
        return base
    if kind == "feed":
        # Try a common feed location; many sites expose /feed or /rss
        return f"{base}/feed"
    paths = {
        "pricing": "/pricing",
        "product": "/product",
        "changelog": "/changelog",
        "careers": "/careers",
    }
    return base + paths.get(kind, "")


def _classify_change(text: str) -> tuple[str, float]:
    """Deterministic change classification from the added-token content."""
    scores: list[tuple[str, float]] = []
    for cls, (pattern, base) in _CLASS_KEYWORDS.items():
        if re.search(pattern, text, re.I):
            scores.append((cls, base))
    if not scores:
        return "unknown", 0.5
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[0][0], scores[0][1]


async def scan_target(comp: dict, kind: str) -> dict:
    """Fetch + diff ONE target of ONE competitor. Pure deterministic; no LLM."""
    url = _target_url(comp, kind)
    ok, text, title = await _fetch_target(url)
    if not ok:
        return {
            "ok": False,
            "competitor_id": comp.get("id"),
            "competitor": comp.get("name"),
            "kind": kind,
            "url": url,
            "error": text or "fetch failed",
        }
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    prev = _load_snapshot(comp.get("id", ""), kind)
    prev_text = (prev or {}).get("content", "")
    snap = {
        "fetched_at": _now(),
        "url": url,
        "title": title,
        "content": text,
        "content_hash": content_hash,
    }
    _save_snapshot(comp.get("id", ""), kind, snap)
    if prev is None:
        # First observation = baseline. Store it, no change event. This keeps
        # detection reproducible: a change only exists relative to a prior snapshot.
        return {
            "ok": True,
            "changed": False,
            "baseline": True,
            "competitor_id": comp.get("id"),
            "kind": kind,
        }
    changed = _meaningful_delta(prev_text, text, kind)
    if not changed:
        return {
            "ok": True,
            "changed": False,
            "competitor_id": comp.get("id"),
            "kind": kind,
        }
    # Build the change event from the added content only (deterministic).
    old_n = _normalize_text(prev_text)
    new_n = _normalize_text(text)
    added_tokens = _tokenize(new_n) - _tokenize(old_n)
    added_text = _extract_added_snippet(text, prev_text)
    cls, _ = _classify_change(added_text)
    tier = comp.get("tier", "tier_2")
    significance = _score_significance(kind, cls, tier, added_tokens)
    event = {
        "id": uuid.uuid4().hex[:12],
        "competitor_id": comp.get("id"),
        "competitor": comp.get("name"),
        "kind": kind,
        "url": url,
        "tier": tier,
        "classification": cls,
        "significance": significance,
        "changed_at": _now(),
        "added_tokens": sorted(added_tokens)[:40],
        "snippet": added_text[:600],
        "prev_hash": (prev or {}).get("content_hash", ""),
        "new_hash": content_hash,
        "dedup_key": hashlib.sha256(
            f"{comp.get('id')}:{kind}:{cls}:{sorted(added_tokens)}".encode("utf-8")
        ).hexdigest()[:16],
    }
    return {"ok": True, "changed": True, "event": event}


def _extract_added_snippet(new_text: str, old_text: str) -> str:
    """Best-effort: return the section of new text around the first added line."""
    old_lines = [l.strip() for l in (old_text or "").splitlines() if l.strip()]
    old_set = set(old_lines)
    for l in new_text.splitlines():
        l = l.strip()
        if l and l not in old_set and not any(n in l.lower() for n in _NOISE_TOKENS):
            return l
    return new_text[:600]


def _score_significance(
    kind: str, cls: str, tier: str, added_tokens: set[str]
) -> float:
    score = _SIG_WEIGHTS.get(kind, 1.0)
    if cls == "pricing":
        score += 2.0
    if cls == "acquisition_partnership":
        score += 1.5
    if cls == "product_feature":
        score += 0.5
    # tier_2 only counts major changes: pricing/product/acquisition
    if tier == "tier_2" and cls not in (
        "pricing",
        "product_feature",
        "acquisition_partnership",
    ):
        score *= 0.5
    # Cap 1..5
    return round(min(5.0, max(1.0, score)), 1)


async def scan_competitor(comp: dict, include: set[str] | None = None) -> list[dict]:
    """Scan all targets of one competitor, returning change events."""
    events: list[dict] = []
    kinds = [k for k in comp.get("targets", []) if include is None or k in include]
    results = await asyncio.gather(
        *(scan_target(comp, k) for k in kinds), return_exceptions=True
    )
    for r in results:
        if isinstance(r, Exception):
            log.warning("intel scan target failed for %s: %s", comp.get("name"), r)
            continue
        if r.get("changed"):
            events.append(r["event"])
    return events


async def scan_all(include: set[str] | None = None) -> dict:
    """Scan all enabled competitors. Failures in one provider never kill the
    fan-out. Returns {scanned, changed, events, errors}."""
    reg = list_competitors()
    enabled = [c for c in reg if c.get("enabled", True)]
    events: list[dict] = []
    errors = []
    results = await asyncio.gather(
        *(scan_competitor(c, include) for c in enabled), return_exceptions=True
    )
    for c, r in zip(enabled, results):
        if isinstance(r, Exception):
            errors.append({"competitor": c.get("name"), "error": str(r)})
            continue
        events.extend(r)
    # Persist
    if events:
        seen: set[str] = set()
        deduped = []
        for ev in events:
            if ev["dedup_key"] in seen:
                continue
            seen.add(ev["dedup_key"])
            deduped.append(ev)
        _append_changes(deduped)
        events = deduped
    return {
        "scanned": len(enabled),
        "changed": len(events),
        "events": events,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Intelligence layer — curation + "so what" (only LLM seam)
# ---------------------------------------------------------------------------
def _dedupe_events(events: list[dict], cap: int = 15) -> list[dict]:
    """Deduplicate by dedup_key + significance-cap to ~10-15 actionable items.
    This is the 'healthy = 10-15 alerts, not 50-200' curation rule."""
    seen: set[str] = set()
    out: list[dict] = []
    for ev in sorted(events, key=lambda e: e.get("significance", 0), reverse=True):
        key = ev.get("dedup_key") or f"{ev.get('competitor_id')}:{ev.get('kind')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
        if len(out) >= cap:
            break
    return out


def _so_what_deterministic(ev: dict) -> str:
    """Deterministic 'so what' — the fallback when no LLM is available. Rule-based
    and defensible, mirrors what a CI analyst would say."""
    cls = ev.get("classification", "unknown")
    comp = ev.get("competitor", "the competitor")
    kind = ev.get("kind", "")
    sig = ev.get("significance", 0)
    snippet = (ev.get("snippet") or "")[:160]
    if cls == "pricing":
        why = f"{comp} changed its pricing signal on their {kind} page."
        rec = "Re-check your pricing positioning and flag to sales for win/loss impact."
    elif cls == "product_feature":
        why = f"{comp} appears to have shipped or announced a product/feature change."
        rec = "Add the change to your competitive matrix and alert product marketing."
    elif cls == "acquisition_partnership":
        why = f"{comp} shows acquisition or partnership signals."
        rec = "Treat this as a market-shape change; brief leadership this week."
    elif cls == "hiring":
        why = f"{comp} posted hiring/job signals."
        rec = "Directional: growth or pivot. Note, low priority."
    elif cls == "marketing_content":
        why = f"{comp} published marketing content."
        rec = "Low priority; log for the weekly digest."
    else:
        why = f"{comp} changed its {kind} page."
        rec = "Review the snippet for strategic relevance."
    if sig >= 4:
        rec += " HIGH SIGNAL."
    return f"{why} {rec} Snippet: {snippet}"


class IntelligenceSynthesizer:
    """Provider-independent 'so what' generator.

    Chain: configured remote model -> local model -> deterministic fallback.
    The detector already decided WHAT changed; this only interprets the delta.
    Deterministic fallback is the built-in guarantee that the whole subsystem
    works with zero external calls.
    """

    def __init__(self, remote_complete=None, local_complete=None):
        self._remote_complete = remote_complete
        self._local_complete = local_complete

    async def synthesize(self, ev: dict) -> str:
        """Return (why_it_matters, recommended_action) prose for one change.
        Records which provider actually produced it (for truthful digest
        attribution: 'deterministic' vs 'remote' vs 'local')."""
        prompt = self._build_prompt(ev)
        # 1) configured remote
        if self._remote_complete is not None:
            try:
                text = await self._remote_complete(prompt)
                if text and len(text.strip()) > 10:
                    _set_synth_provider("remote")
                    return text.strip()
            except Exception as exc:
                log.warning("intel remote synthesize failed: %s", exc)
        # 2) local model
        if self._local_complete is not None:
            try:
                text = await self._local_complete(prompt)
                if text and len(text.strip()) > 10:
                    _set_synth_provider("local")
                    return text.strip()
            except Exception as exc:
                log.warning("intel local synthesize failed: %s", exc)
        # 3) deterministic
        _set_synth_provider("deterministic")
        return _so_what_deterministic(ev)

    @staticmethod
    def _build_prompt(ev: dict) -> str:
        return (
            "You are a competitive intelligence analyst. Interpret ONLY this "
            "confirmed change (do not decide whether it changed; it already did). "
            "Give 1-2 sentences: why it matters to a business competing with this "
            "company, and one recommended action.\n\n"
            f"Competitor: {ev.get('competitor')}\n"
            f"Page: {ev.get('kind')}\n"
            f"Classification: {ev.get('classification')}\n"
            f"Change snippet: {ev.get('snippet')}"
        )


_SYNTHESIZER = IntelligenceSynthesizer()


async def generate_digest(
    since: str | None = None,
    cap: int = 15,
    synthesizer: IntelligenceSynthesizer | None = None,
) -> dict:
    """Build the curated digest from stored change events. `since` ISO ts filter.
    Returns {digest_id, generated_at, items: [{...}], delivery: [...]}."""
    events = _load_changes()
    if since:
        events = [e for e in events if e.get("changed_at", "") >= since]
    curated = _dedupe_events(events, cap=cap)
    synth = synthesizer or _SYNTHESIZER
    items = []
    for ev in curated:
        so_what = await synth.synthesize(ev)
        items.append(
            {
                "id": ev.get("id"),
                "competitor": ev.get("competitor"),
                "kind": ev.get("kind"),
                "classification": ev.get("classification"),
                "significance": ev.get("significance"),
                "url": ev.get("url"),
                "snippet": ev.get("snippet"),
                "what_changed": (ev.get("snippet") or "")[:200],
                "so_what": so_what,
            }
        )
    digest = {
        "id": uuid.uuid4().hex[:12],
        "generated_at": _now(),
        "item_count": len(items),
        "items": items,
        "provider": _last_synth_provider(),
    }
    _save_digest(digest)
    return digest


_last_provider = {"name": "deterministic"}


def _set_synth_provider(name: str) -> None:
    _last_provider["name"] = name


def _last_synth_provider() -> str:
    return _last_provider["name"]


def _save_digest(digest: dict) -> None:
    _ensure_dirs()
    p = _paths()["digests"] / f"{digest['id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def list_digests(limit: int = 10) -> list[dict]:
    _ensure_dirs()
    files = sorted(
        _paths()["digests"].glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    out = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def get_digest(digest_id: str) -> dict | None:
    p = _paths()["digests"] / f"{digest_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_changes(limit: int = 100) -> list[dict]:
    return _load_changes(limit=limit)


def get_change(change_id: str) -> dict | None:
    for c in _load_changes(limit=2000):
        if c.get("id") == change_id:
            return c
    return None


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
async def deliver_digest(
    digest: dict,
    channels: list[str] | None = None,
    email_to: str | None = None,
    webhook_url: str | None = None,
) -> dict:
    """Deliver a digest via configured channels. Records each attempt so failures
    are observable/retryable. Channels: email, telegram, slack (webhook)."""
    channels = channels or _configured_channels()
    results = []
    subject = f"Competitive Intel Digest — {len(digest.get('items', []))} changes"
    body = _render_digest_text(digest)
    for ch in channels:
        rec = {"digest_id": digest.get("id"), "channel": ch, "at": _now(), "ok": False}
        try:
            if ch == "email":
                rec["ok"] = await _deliver_email(subject, body, email_to)
            elif ch == "telegram":
                rec["ok"] = await _deliver_telegram(body)
            elif ch == "slack":
                rec["ok"] = await _deliver_slack(body, webhook_url)
            else:
                rec["error"] = f"unknown channel {ch}"
        except Exception as exc:
            rec["error"] = str(exc)
            log.warning("intel deliver %s failed: %s", ch, exc)
        _append_delivery(rec)
        results.append(rec)
    return {"ok": any(r["ok"] for r in results), "deliveries": results}


def _configured_channels() -> list[str]:
    channels = []
    if _email_configured():
        channels.append("email")
    if _telegram_configured():
        channels.append("telegram")
    if os.getenv("INTEL_SLACK_WEBHOOK"):
        channels.append("slack")
    return channels


def _email_configured() -> bool:
    from swarm_os.services import email_service as es

    try:
        if hasattr(es, "email_config_status"):
            return bool(es.email_config_status().get("configured"))
    except Exception:
        pass
    # Fallback: explicit recipient env means the operator wants email delivery.
    return bool(os.getenv("INTEL_EMAIL_TO"))


def _telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN"))


def _render_digest_text(digest: dict) -> str:
    lines = [f"# Competitive Intel Digest — {len(digest.get('items', []))} changes"]
    for i, it in enumerate(digest.get("items", []), 1):
        lines.append(
            f"\n{i}. {it.get('competitor')} · {it.get('kind')} · {it.get('classification')} (sig {it.get('significance')})"
        )
        lines.append(f"   What changed: {it.get('what_changed')}")
        lines.append(f"   Why it matters: {it.get('so_what')}")
        lines.append(f"   {it.get('url')}")
    return "\n".join(lines)


async def _deliver_email(subject: str, body: str, email_to: str | None) -> bool:
    from swarm_os.services import email_service as es

    to = email_to or os.getenv("INTEL_EMAIL_TO")
    if not to:
        return False

    def _send():
        draft = es.email_draft(to=to, subject=subject, body=body)
        if not draft.get("ok"):
            return False
        res = es.email_send(draft["send_token"], confirmed=True)
        return bool(res.get("ok"))

    # email_send does blocking SMTP/Gmail I/O — never run it on the event loop.
    return await asyncio.to_thread(_send)


async def _deliver_telegram(body: str) -> bool:
    from swarm_os.services import telegram_center as tc

    return tc.notify(f"<b>Competitive Intel</b>\n<pre>{body[:3000]}</pre>")


_SLACK_CLIENT = None


def _get_slack_client():
    global _SLACK_CLIENT
    if _SLACK_CLIENT is None or _SLACK_CLIENT.is_closed:
        import httpx

        _SLACK_CLIENT = httpx.AsyncClient(timeout=15.0)
    return _SLACK_CLIENT


async def _deliver_slack(body: str, webhook_url: str | None) -> bool:
    url = webhook_url or os.getenv("INTEL_SLACK_WEBHOOK")
    if not url:
        return False
    client = _get_slack_client()
    r = await client.post(url, json={"text": body[:3900]})
    return r.status_code == 200


# ---------------------------------------------------------------------------
# Full run (collect -> digest -> deliver)
# ---------------------------------------------------------------------------
async def run_intel(
    channels: list[str] | None = None,
    email_to: str | None = None,
    webhook_url: str | None = None,
    include: set[str] | None = None,
    cap: int = 15,
) -> dict:
    """End-to-end: scan all competitors, generate digest, deliver it."""
    scan = await scan_all(include=include)
    if not scan.get("changed"):
        return {
            "ok": True,
            "changed": 0,
            "message": "no changes detected",
            "scanned": scan.get("scanned"),
        }
    digest = await generate_digest(cap=cap)
    delivery = await deliver_digest(
        digest, channels=channels, email_to=email_to, webhook_url=webhook_url
    )
    return {
        "ok": True,
        "changed": scan.get("changed"),
        "digest_id": digest.get("id"),
        "item_count": len(digest.get("items", [])),
        "delivery": delivery,
    }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Weekly scheduler — configurable cadence, no duplicate runs, history preserved
# ---------------------------------------------------------------------------
def _last_full_run() -> dict | None:
    """The most recent scheduled full run, read from the delivery/change trail.
    Prevents duplicate runs by tracking last_run per cadence."""
    p = _paths()["data_dir"] / "last_run.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_last_run(record: dict) -> None:
    _ensure_dirs()
    p = _paths()["data_dir"] / "last_run.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def intel_due_now(cadence_hours: float = 168.0) -> bool:
    """True if a scheduled run is due (default: weekly = 168h since last run).
    Never runs twice inside the window — duplicate-run protection."""
    last = _last_full_run()
    if last is None:
        return True
    try:
        import datetime as _dt

        last_ts = _dt.datetime.fromisoformat(last.get("at", "")).timestamp()
        return (time.time() - last_ts) >= cadence_hours * 3600
    except Exception:
        return True


async def intel_daemon(
    interval_seconds: float = 3600.0,
    cadence_hours: float = 168.0,
    channels: list[str] | None = None,
) -> None:
    """Background daemon: every `interval_seconds`, if a full run is due (weekly
    by default), run it. Failures log; heartbeat-free (runs are low-frequency).
    Channels default to configured (email/telegram/slack)."""
    log.info(
        "intel daemon started (cadence %sh, check %ss)", cadence_hours, interval_seconds
    )
    while True:
        try:
            if intel_due_now(cadence_hours):
                log.info("intel daemon: weekly run due, running...")
                res = await run_intel(channels=channels)
                _save_last_run({"at": _now(), "result": res})
                log.info("intel daemon: run complete: %s", res)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("intel daemon run failed: %s", exc)
        await asyncio.sleep(interval_seconds)

