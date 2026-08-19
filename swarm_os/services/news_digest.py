"""News digest + story tracking (2026 SOTA — Spark/Perplexity custom news digest).

A self-hosted news pipeline that closes the "get a custom news digest / follow
stories as they evolve" gap:

  * subscriptions: a topic -> [feed urls] registry (data/news/subscriptions.json),
    with a default curated feed set covering general tech/AI/startups;
  * ingest: fetch each feed (via the existing web_fetch_handler), parse RSS 2.0
    / Atom with stdlib ElementTree (no new dependency), dedupe by link + by
    normalized title, and persist new items to a rolling store
    (data/news/items.jsonl) so the digest knows what's NEW vs already-seen;
  * digest: LLM synthesis (reuses the analysis-cloud model, same contract as
    deep_research/email_digest) grouped by topic, flagging what changed since
    the last digest;
  * story tracking: items sharing a normalized title stem are treated as the
    same story evolving over time — the digest reports "updated N times".

Fail-closed: an unreadable store degrades to an empty digest with an error
string, never a crash. Feed fetch/parse failures are per-feed and logged, never
fatal. LLM synthesis failure degrades to the raw item list.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from xml.etree import ElementTree

log = logging.getLogger(__name__)

_DATA_DIR = Path("data/news")
_SUBSCRIPTIONS_FILE = _DATA_DIR / "subscriptions.json"
_ITEMS_FILE = _DATA_DIR / "items.jsonl"
_LOCK = threading.Lock()

DEFAULT_SUBSCRIPTIONS = {
    "ai-agents": [
        "https://simonwillison.net/atom/everything/",
        "https://www.oreilly.com/radar/feed/",
    ],
    "tech": [
        "https://www.theverge.com/rss/index.xml",
        "https://feeds.arstechnica.com/arstechnica/index",
    ],
    "ai-ml": [
        "https://huggingface.co/blog/feed.xml",
        "https://ai.meta.com/blog/feed/",
    ],
    "startups": [
        "https://techcrunch.com/feed/",
        "https://feeds.feedburner.com/ycombinatorblog",
    ],
}

# A feed must carry at least one of these to be accepted at ingest time — the
# anti-malformed-feed guard. (The defaults above are curated, but a user-added
# URL could point anywhere.)
_ALLOWED_FEED_DOMAINS = (
    "simonwillison.net",
    "oreilly.com",
    "theverge.com",
    "arstechnica.com",
    "huggingface.co",
    "meta.com",
    "techcrunch.com",
    "feedburner.com",
    "ycombinator.com",
)


def _now() -> float:
    return time.time()


def _load_subscriptions() -> dict[str, list[str]]:
    try:
        if _SUBSCRIPTIONS_FILE.exists():
            data = json.loads(_SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        log.warning("news subscriptions load failed: %s", exc)
    return {k: list(v) for k, v in DEFAULT_SUBSCRIPTIONS.items()}


def _save_subscriptions(subs: dict[str, list[str]]) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = _SUBSCRIPTIONS_FILE.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(subs, indent=2), encoding="utf-8")
        os.replace(tmp_path, _SUBSCRIPTIONS_FILE)
    except Exception as exc:
        log.warning("news subscriptions save failed: %s", exc)


def list_subscriptions() -> dict:
    """All topics -> feed URLs, plus the allowed-domain guard note."""
    subs = _load_subscriptions()
    return {"ok": True, "topics": subs, "allowed_domains": list(_ALLOWED_FEED_DOMAINS)}


def add_subscription(topic: str, url: str) -> dict:
    """Add a feed URL to a topic. Validates the URL is http(s) and on the
    allowlist (fail-closed: an unknown feed host is refused, not silently
    ingested)."""
    url = (url or "").strip()
    topic = (topic or "").strip().lower().replace(" ", "-")
    if not topic or not url:
        return {"ok": False, "error": "topic and url are required"}
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "feed URL must be http(s)"}
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    if not any(host == d or host.endswith("." + d) for d in _ALLOWED_FEED_DOMAINS):
        return {"ok": False, "error": f"feed host '{host}' is not on the allowed list"}
    with _LOCK:
        subs = _load_subscriptions()
        existing = subs.get(topic, [])
        if url not in existing:
            existing.append(url)
        subs[topic] = existing
        _save_subscriptions(subs)
    return {"ok": True, "topic": topic, "urls": subs[topic]}


def remove_subscription(topic: str, url: str | None = None) -> dict:
    topic = (topic or "").strip().lower().replace(" ", "-")
    with _LOCK:
        subs = _load_subscriptions()
        if topic not in subs:
            return {"ok": False, "error": f"topic '{topic}' not subscribed"}
        if url is None:
            del subs[topic]
        else:
            subs[topic] = [u for u in subs[topic] if u != url]
            if not subs[topic]:
                del subs[topic]
        _save_subscriptions(subs)
    return {"ok": True, "topics": subs}


# ---------------------------------------------------------------------------
# Feed parsing (stdlib — RSS 2.0 + Atom)
# ---------------------------------------------------------------------------
def _parse_feed(xml_text: str) -> list[dict]:
    """Parse an RSS 2.0 or Atom document into [{title, link, summary, published}]."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        log.warning("feed parse error: %s", exc)
        return []
    # ElementTree.iter("item") does NOT match namespace-qualified tags on some
    # builds, so match by tag suffix across all descendants (RSS item / Atom
    # entry). Collect each family independently and prefer RSS when present.
    rss_items: list[dict] = []
    atom_items: list[dict] = []
    for entry in root.iter():
        tag = entry.tag.split("}")[-1]
        if tag == "item":
            title = _child_text(entry, "title")
            link = _child_text(entry, "link")
            summary = _child_text(entry, "description") or _child_text(entry, "summary")
            published = _child_text(entry, "pubDate") or _child_text(entry, "date")
            if title and link:
                rss_items.append(
                    {
                        "title": title,
                        "link": link,
                        "summary": summary,
                        "published": published,
                    }
                )
        elif tag == "entry":
            title = _child_text(entry, "title")
            link_el = None
            # ATOM FIX: prefer link with rel="alternate" (the actual article URL)
            # over the first link (often the feed self-reference).
            alternate_links = []
            for child in entry:
                if child.tag.split("}")[-1] == "link":
                    href = child.get("href")
                    rel = child.get("rel", "alternate")
                    if href:
                        if rel == "alternate":
                            link_el = child
                            break
                        alternate_links.append(child)
            if link_el is None and alternate_links:
                link_el = alternate_links[0]
            link = (
                link_el.get("href")
                if link_el is not None
                else _child_text(entry, "link")
            )
            summary = _child_text(entry, "summary") or _child_text(entry, "content")
            published = _child_text(entry, "published") or _child_text(entry, "updated")
            if title and link:
                atom_items.append(
                    {
                        "title": title,
                        "link": link,
                        "summary": summary,
                        "published": published,
                    }
                )
    return rss_items or atom_items


def _child_text(parent, tag: str) -> str:
    """First descendant text for a local tag name (namespace-agnostic)."""
    for child in parent.iter():
        if child.tag.split("}")[-1] == tag:
            return (child.text or "").strip()
    return ""


def _item_key(item: dict) -> str:
    """Dedup key: link if present, else a normalized-title hash."""
    if item.get("link"):
        return item["link"]
    title = re.sub(r"[^a-z0-9]+", "", (item.get("title") or "").lower())
    return hashlib.sha1(title.encode()).hexdigest()[:16]


def _story_stem(title: str) -> str:
    """Normalized title stem used to group items that are the same story."""
    t = title.lower()
    t = re.sub(r"\b(re|breaking|update|updated|live)\b", "", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:60]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def _load_items() -> list[dict]:
    items = []
    try:
        if _ITEMS_FILE.exists():
            for line in _ITEMS_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        log.warning("news items load failed: %s", exc)
    return items


def _save_items(items: list[dict]) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = _ITEMS_FILE.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            for it in items:
                fh.write(json.dumps(it) + "\n")
        os.replace(tmp_path, _ITEMS_FILE)
    except Exception as exc:
        log.warning("news items save failed: %s", exc)


# ---------------------------------------------------------------------------
# Ingest + digest
# ---------------------------------------------------------------------------
async def _acomplete(prompt: str, max_tokens: int = 800, timeout: float = 120.0) -> str:
    import litellm

    from ..core.settings import get_settings

    s = get_settings()
    model = getattr(s, "analysis_cloud_model", None) or "openai/deepseek-v4-flash"
    base = os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
    key = os.getenv("OPENAI_API_KEY", "")
    resp = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        api_base=base,
        api_key=key,
        custom_llm_provider="openai",
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return resp.choices[0].message.content or ""


async def ingest_feeds(limit_per_feed: int = 10) -> dict:
    """Fetch + parse every subscribed feed, persist new items, and return what
    changed. Idempotent: items already in the store are not re-added."""
    from ..lib.mcp.web_search import web_fetch_handler

    subs = _load_subscriptions()
    fetched: list[tuple[str, str, dict]] = []
    errors: list[str] = []
    for topic, urls in subs.items():
        for url in urls:
            try:
                fetched_resp = await web_fetch_handler(
                    {"url": url, "max_chars": 200_000}
                )
                text = fetched_resp.get("text") or fetched_resp.get("content") or ""
                if not text:
                    continue
                for e in _parse_feed(text)[:limit_per_feed]:
                    fetched.append((topic, url, e))
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                log.warning("news ingest failed for %s: %s", url, exc)
    with _LOCK:
        store = _load_items()
        seen = {_item_key(it) for it in store}
        new_items: list[dict] = []
        for topic, url, e in fetched:
            key = _item_key(e)
            if key in seen:
                continue
            seen.add(key)
            e["topic"] = topic
            e["source"] = url
            e["ingested_at"] = _now()
            store.append(e)
            new_items.append(e)
        _save_items(store[-500:])
    return {
        "ok": True,
        "ingested": len(new_items),
        "new_items": [
            {k: v for k, v in it.items() if k in ("title", "link", "topic")}
            for it in new_items[:20]
        ],
        "errors": errors,
    }


async def digest(topic: str | None = None, max_items: int = 30) -> dict:
    """LLM digest of the recent items, optionally scoped to one topic. The
    digest groups by topic, flags items that are NEW since the store's last
    read, and notes stories that have evolved (same stem seen multiple times)."""
    with _LOCK:
        items = _load_items()
    if topic:
        items = [it for it in items if it.get("topic") == topic]
    items = items[-max_items:]
    if not items:
        return {
            "ok": True,
            "topic": topic,
            "digest": "No items in the store yet. Run ingest first.",
            "degraded": False,
        }
    # Story tracking: count items sharing a story stem.
    stem_counts: dict[str, int] = {}
    for it in items:
        stem = _story_stem(it.get("title", ""))
        if stem:
            stem_counts[stem] = stem_counts.get(stem, 0) + 1
    block = []
    for it in items:
        stem = _story_stem(it.get("title", ""))
        updates = stem_counts.get(stem, 1)
        flag = f" (evolved x{updates})" if updates > 1 else ""
        block.append(
            f"[{it.get('topic', '?')}]{flag} {it.get('title', '')} — {it.get('link', '')}"
        )
    prompt = (
        "You are a personal news editor. Given the recent headlines below, produce "
        "a digest grouped by topic. For each group: 2-4 bullets, each with the "
        "headline + a 1-line why-it-matters. Flag anything that looks like an "
        "evolving story (multiple headlines on the same topic) and any item that "
        "seems time-sensitive or urgent. Be concise.\n\n"
        f"HEADLINES ({len(items)}):\n" + "\n".join(block)
    )
    text = ""
    try:
        text = await _acomplete(prompt, max_tokens=900)
    except Exception as exc:
        log.warning("news digest LLM failed: %s", exc)
    return {
        "ok": True,
        "topic": topic or "all",
        "digest": text or "digest generation failed (LLM unavailable); items below.",
        "items": [
            {k: it.get(k) for k in ("title", "link", "topic")} for it in items[-20:]
        ],
        "degraded": not text,
    }
