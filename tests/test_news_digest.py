"""Tests for the news digest + story tracking service.

Feed fetching (web_fetch_handler) and LLM synthesis are mocked — the parsing,
dedup, story-stem, subscription, and digest logic is exercised deterministically
with real RSS/Atom XML. No network.
"""

import json

import pytest

from swarm_os.services import news_digest as nd

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Test</title>
  <item><title>Breaking: Story Alpha</title><link>http://x.com/a1</link>
    <description>first</description><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
  <item><title>Story Beta</title><link>http://x.com/b1</link>
    <description>second</description></item>
</channel></rss>
"""

ATOM_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Atom Item</title><link href="http://x.com/atom1"/><summary>s</summary></entry>
</feed>
"""


@pytest.fixture(autouse=True)
def _isolate_store(monkeypatch, tmp_path):
    monkeypatch.setattr(nd, "_DATA_DIR", tmp_path / "news")
    monkeypatch.setattr(
        nd, "_SUBSCRIPTIONS_FILE", tmp_path / "news" / "subscriptions.json"
    )
    monkeypatch.setattr(nd, "_ITEMS_FILE", tmp_path / "news" / "items.jsonl")
    yield


def test_parse_feed_rss():
    items = nd._parse_feed(RSS_XML)
    assert len(items) == 2
    assert items[0]["title"] == "Breaking: Story Alpha"
    assert items[0]["link"] == "http://x.com/a1"
    assert items[1]["summary"] == "second"


def test_parse_feed_atom():
    items = nd._parse_feed(ATOM_XML)
    assert len(items) == 1
    assert items[0]["title"] == "Atom Item"
    assert items[0]["link"] == "http://x.com/atom1"


def test_parse_feed_malformed():
    assert nd._parse_feed("this is not xml") == []


def test_item_key_uses_link_then_title_hash():
    a = {"title": "X", "link": "http://a"}
    b = {"title": "X", "link": "http://a"}  # same link -> same key
    assert nd._item_key(a) == nd._item_key(b)
    no_link_1 = {"title": "Hello World", "link": ""}
    no_link_2 = {"title": "hello world", "link": None}
    assert nd._item_key(no_link_1) == nd._item_key(no_link_2)


def test_story_stem_groups_evolving_story():
    s1 = nd._story_stem("Story Alpha")
    s2 = nd._story_stem("Update: Story Alpha")
    s3 = nd._story_stem("Completely different thing")
    assert s1 == s2
    assert s1 != s3


def test_add_subscription_allowlisted(monkeypatch):
    res = nd.add_subscription("my-topic", "https://techcrunch.com/feed/")
    assert res["ok"] is True
    assert "https://techcrunch.com/feed/" in res["urls"]


def test_add_subscription_rejects_unknown_host(monkeypatch):
    res = nd.add_subscription("my-topic", "https://evil.example.com/feed")
    assert res["ok"] is False
    assert "allowed" in res["error"]


def test_add_subscription_rejects_non_http(monkeypatch):
    res = nd.add_subscription("my-topic", "file:///etc/passwd")
    assert res["ok"] is False


def test_remove_subscription_topic_and_url(monkeypatch):
    nd.add_subscription("t", "https://techcrunch.com/feed/")
    res = nd.remove_subscription("t", "https://techcrunch.com/feed/")
    assert res["ok"] is True
    assert "t" not in res["topics"]


def test_add_subscription_does_not_mutate_default_subs(monkeypatch):
    original = [list(v) for v in nd.DEFAULT_SUBSCRIPTIONS.values()]
    res = nd.add_subscription("ai-agents", "https://oreilly.com/radar/feed/")
    assert res["ok"] is True
    assert all(
        list(v) == o for v, o in zip(nd.DEFAULT_SUBSCRIPTIONS.values(), original)
    )


def test_ingest_feeds_deduplicates(monkeypatch):
    calls = {"n": 0}

    async def fake_fetch(params):
        calls["n"] += 1
        return {"ok": True, "text": RSS_XML}

    monkeypatch.setattr("swarm_os.lib.mcp.web_search.web_fetch_handler", fake_fetch)
    res = _run(nd.ingest_feeds())
    assert res["ok"] is True
    assert res["ingested"] == 2
    # Second ingest: same feed, items already in store -> nothing new.
    res2 = _run(nd.ingest_feeds())
    assert res2["ingested"] == 0


def test_ingest_feeds_does_not_hold_lock_across_fetch(monkeypatch):
    """The store lock must not be held while a network fetch is in flight —
    holding a threading.Lock across an await freezes any same-loop consumer
    (e.g. digest()) for the whole fetch storm."""
    lock_states = []

    async def fake_fetch(params):
        lock_states.append(nd._LOCK.locked())
        return {"ok": True, "text": RSS_XML}

    monkeypatch.setattr("swarm_os.lib.mcp.web_search.web_fetch_handler", fake_fetch)
    res = _run(nd.ingest_feeds())
    assert res["ok"] is True
    assert lock_states, "fetch handler was never called"
    assert all(held is False for held in lock_states)


def test_digest_llm_down_degrades(monkeypatch):
    nd._DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(nd._ITEMS_FILE, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"title": "A", "link": "http://a", "topic": "tech"}) + "\n")

    async def boom(prompt, **kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(nd, "_acomplete", boom)
    res = _run(nd.digest())
    assert res["ok"] is True
    assert res["degraded"] is True
    assert "LLM unavailable" in res["digest"]
    assert res["items"]


def test_digest_empty_store():
    res = _run(nd.digest())
    assert res["ok"] is True
    assert "No items" in res["digest"]


def test_digest_groups_and_notes_evolving_story(monkeypatch):
    nd._DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(nd._ITEMS_FILE, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"title": "Story Alpha", "link": "http://a", "topic": "tech"})
            + "\n"
        )
        fh.write(
            json.dumps(
                {"title": "Update: Story Alpha", "link": "http://b", "topic": "tech"}
            )
            + "\n"
        )
        fh.write(
            json.dumps({"title": "Other", "link": "http://c", "topic": "ai"}) + "\n"
        )

    async def fake_complete(prompt, **kw):
        # The prompt should carry the evolved-story flag.
        assert "evolved" in prompt or "HEADLINES" in prompt
        return "DIGEST OK"

    monkeypatch.setattr(nd, "_acomplete", fake_complete)
    res = _run(nd.digest())
    assert res["digest"] == "DIGEST OK"


def test_digest_topic_filter(monkeypatch):
    nd._DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(nd._ITEMS_FILE, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"title": "A", "link": "http://a", "topic": "tech"}) + "\n")
        fh.write(json.dumps({"title": "B", "link": "http://b", "topic": "ai"}) + "\n")

    async def fake_complete(prompt, **kw):
        return "ok"

    monkeypatch.setattr(nd, "_acomplete", fake_complete)
    res = _run(nd.digest(topic="ai"))
    assert res["topic"] == "ai"
    assert all(it["topic"] == "ai" for it in res["items"])
    assert len(res["items"]) == 1


def _run(coro):
    import asyncio

    return asyncio.run(coro)
