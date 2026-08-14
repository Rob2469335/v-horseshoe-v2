"""Tests for the /books knowledge-base routes.

The real manifest is gitignored (data/), so these tests build a tiny temp
manifest and inject a BooksService bound to it — they never touch production
data and pass on a clean checkout.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import json
from pathlib import Path

import swarm_os.api.books as books_mod
from swarm_os.services.books_service import BooksService


SAMPLE = {
    "generated_at": "2026-08-14T00:00:00",
    "books": [
        {
            "slug": "the-e-myth-revisited",
            "title": "The E-Myth Revisited",
            "author": "Michael Gerber",
            "track": "income",
            "track_label": "INCOME",
            "priority": "READ NOW",
            "scores": {"ai": 9, "income": 10, "technical": 2, "business": 10, "apply": 9, "long": 3},
            "legitimate_source": "Library / summary",
            "public_domain": False,
            "summary_status": "complete",
            "ai_relevance": "high",
            "best_parts": ["Work on the business, not just in it."],
            "warnings": ["Some dated examples."],
            "freelancer_translation": "Productize your service into repeatable systems.",
        },
        {
            "slug": "the-lean-startup",
            "title": "The Lean Startup",
            "author": "Eric Ries",
            "track": "mindset",
            "track_label": "MINDSET",
            "priority": "READ LATER",
            "scores": {"ai": 6, "income": 7, "technical": 3, "business": 9, "apply": 8, "long": 6},
            "legitimate_source": "Library / summary",
            "public_domain": False,
            "summary_status": "complete",
            "ai_relevance": "medium",
            "best_parts": ["Build-measure-learn loop."],
            "warnings": [],
            "freelancer_translation": "Validate offers cheaply before scaling.",
        },
    ],
}


def _make_client(tmp_path: Path, books: list | None = None) -> TestClient:
    manifest = tmp_path / "manifest.json"
    payload = dict(SAMPLE)
    if books is not None:
        payload["books"] = books
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    svc = BooksService(manifest_path=manifest)
    books_mod.get_books_service = lambda: svc

    app = FastAPI()
    app.include_router(books_mod.router)
    return TestClient(app)


def test_books_list_all(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["count"] == 2
    assert {"income", "mindset"} <= set(j["tracks"])


def test_books_list_filters(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books", params={"track": "income"})
        assert r.json()["count"] == 1
        assert r.json()["books"][0]["slug"] == "the-e-myth-revisited"
        r = c.get("/books", params={"priority": "READ NOW"})
        assert r.json()["count"] == 1


def test_get_book(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books/the-e-myth-revisited")
    assert r.status_code == 200
    assert r.json()["book"]["author"] == "Michael Gerber"


def test_get_book_missing(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books/not-a-book")
    assert r.status_code == 404


def test_search_matches_fields(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books/search", params={"q": "productize"})
    j = r.json()
    assert j["ok"] is True
    assert j["count"] == 1
    assert j["results"][0]["slug"] == "the-e-myth-revisited"


def test_search_empty_query_returns_nothing(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books/search", params={"q": "zzz-no-match"})
    assert r.json()["count"] == 0


def test_search_substring_token_is_not_a_false_hit(tmp_path):
    """Regression: 'no' must not match the word 'not' — search tokens are
    whole-word matched against the haystack, never substring matches."""
    with _make_client(tmp_path) as c:
        r = c.get("/books/search", params={"q": "no"})
    assert r.json()["count"] == 0
    with _make_client(tmp_path) as c:
        r2 = c.get("/books/search", params={"q": "not"})
    assert r2.json()["count"] == 1
    assert r2.json()["results"][0]["slug"] == "the-e-myth-revisited"


def test_synthesize_returns_fragments(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.post("/books/synthesize", json={"question": "how do I productize an AI automation service?"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["question"].startswith("how do I productize")
    assert len(j["fragments"]) == 2
    assert j["fragments"][0]["slug"] == "the-e-myth-revisited"


def test_missing_manifest_is_fail_closed(tmp_path):
    with _make_client(tmp_path, books=None) as c:
        manifest = tmp_path / "manifest.json"
        manifest.unlink()
        r = c.get("/books")
    assert r.status_code == 503
    assert "manifest" in r.json()["detail"].lower()


def test_invalid_manifest_is_fail_closed(tmp_path):
    with _make_client(tmp_path, books=None) as c:
        (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
        r = c.get("/books")
    assert r.status_code == 503