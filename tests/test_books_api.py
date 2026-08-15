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
    "genres": ["freelancer", "chess"],
    "books": [
        {
            "slug": "the-e-myth-revisited",
            "title": "The E-Myth Revisited",
            "author": "Michael Gerber",
            "genre": "freelancer",
            "track": "income",
            "track_label": "INCOME",
            "tier": None,
            "rating_band": "",
            "year": None,
            "priority": "READ NOW",
            "scores": {
                "ai": 9,
                "income": 10,
                "technical": 2,
                "business": 10,
                "apply": 9,
                "long": 3,
            },
            "legitimate_source": "Library / summary",
            "public_domain": False,
            "summary_status": "complete",
            "ai_relevance": "high",
            "best_parts": ["Work on the business, not just in it."],
            "warnings": ["Some dated examples."],
            "freelancer_translation": "Productize your service into repeatable systems.",
            "beginner_translation": "",
        },
        {
            "slug": "the-lean-startup",
            "title": "The Lean Startup",
            "author": "Eric Ries",
            "genre": "freelancer",
            "track": "mindset",
            "track_label": "MINDSET",
            "tier": None,
            "rating_band": "",
            "year": None,
            "priority": "READ LATER",
            "scores": {
                "ai": 6,
                "income": 7,
                "technical": 3,
                "business": 9,
                "apply": 8,
                "long": 6,
            },
            "legitimate_source": "Library / summary",
            "public_domain": False,
            "summary_status": "complete",
            "ai_relevance": "medium",
            "best_parts": ["Build-measure-learn loop."],
            "warnings": [],
            "freelancer_translation": "Validate offers cheaply before scaling.",
            "beginner_translation": "",
        },
        {
            "slug": "winning-chess-tactics",
            "title": "Winning Chess Tactics",
            "author": "Yasser Seirawan",
            "genre": "chess",
            "track": "chess",
            "track_label": "CHESS",
            "tier": 1,
            "rating_band": "600-1200",
            "year": 1992,
            "priority": "READ NOW",
            "scores": {
                "tactics": 9,
                "strategy": 3,
                "endgames": 1,
                "openings": 1,
                "instruction": 8,
                "exercises": 6,
            },
            "legitimate_source": "Library / summary",
            "public_domain": False,
            "summary_status": "complete",
            "ai_relevance": None,
            "best_parts": ["The double attack and knight fork."],
            "warnings": ["Assumes you know the rules."],
            "freelancer_translation": "",
            "beginner_translation": "Read the first four chapters slowly; cover the solution and try to find the winning move yourself.",
        },
        {
            "slug": "chess-fundamentals",
            "title": "Chess Fundamentals",
            "author": "José Raúl Capablanca",
            "genre": "chess",
            "track": "chess",
            "track_label": "CHESS",
            "tier": 1,
            "rating_band": "1000-1600",
            "year": 1921,
            "priority": "READ LATER",
            "scores": {
                "tactics": 4,
                "strategy": 8,
                "endgames": 7,
                "openings": 4,
                "instruction": 8,
                "exercises": 3,
            },
            "legitimate_source": "Public domain (1921) — Project Gutenberg #33870",
            "public_domain": True,
            "summary_status": "complete",
            "ai_relevance": None,
            "best_parts": ["The principle of the development of the pieces."],
            "warnings": ["Descriptive notation in the original."],
            "freelancer_translation": "",
            "beginner_translation": "SKIP at 500 — mark it for the ~1000 milestone.",
        },
        {
            "slug": "the-woodpecker-method",
            "title": "The Woodpecker Method",
            "author": "Axel Smith and Hans Tikkanen",
            "genre": "chess",
            "track": "chess",
            "track_label": "CHESS",
            "tier": 3,
            "rating_band": "1200-1800",
            "year": 2018,
            "priority": "READ NOW",
            "scores": {
                "tactics": 10,
                "strategy": 2,
                "endgames": 2,
                "openings": 1,
                "instruction": 8,
                "exercises": 10,
            },
            "legitimate_source": "Library / summary",
            "public_domain": False,
            "summary_status": "complete",
            "ai_relevance": None,
            "best_parts": ["Solve one fixed puzzle set in cycles, each faster."],
            "warnings": ["A multi-week program, not a browse."],
            "freelancer_translation": "",
            "beginner_translation": "Start with the first section only; repeat cycles against the clock.",
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
    assert j["count"] == 5
    assert {"income", "mindset"} <= set(j["tracks"])
    assert set(j["genres"]) == {"freelancer", "chess"}


def test_books_list_genre_chess(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books", params={"genre": "chess"})
    j = r.json()
    assert j["count"] == 3
    assert {b["slug"] for b in j["books"]} == {
        "winning-chess-tactics",
        "chess-fundamentals",
        "the-woodpecker-method",
    }
    assert j["books"][0]["tier"] == 1
    assert j["books"][0]["rating_band"] == "600-1200"


def test_books_list_genre_freelancer(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books", params={"genre": "freelancer"})
    assert r.json()["count"] == 2


def test_books_list_tiers_derived_from_data(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books", params={"genre": "chess"})
    j = r.json()
    assert set(j["tiers"]) == {1, 3}
    with _make_client(tmp_path) as c:
        r2 = c.get("/books", params={"genre": "chess", "tier": 3})
    assert r2.json()["count"] == 1
    assert r2.json()["books"][0]["slug"] == "the-woodpecker-method"


def test_books_list_tier_filter(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books", params={"genre": "chess", "tier": 1})
    assert r.json()["count"] == 2
    with _make_client(tmp_path) as c:
        r2 = c.get("/books", params={"genre": "chess", "tier": 9})
    assert r2.json()["count"] == 0


def test_books_list_filters(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books", params={"track": "income"})
        assert r.json()["count"] == 1
        assert r.json()["books"][0]["slug"] == "the-e-myth-revisited"
        r = c.get("/books", params={"priority": "READ NOW"})
        assert r.json()["count"] == 3


def test_get_book(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books/the-e-myth-revisited")
    assert r.status_code == 200
    assert r.json()["book"]["author"] == "Michael Gerber"


def test_get_book_chess_includes_beginner_translation(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books/winning-chess-tactics")
    assert r.status_code == 200
    book = r.json()["book"]
    assert book["genre"] == "chess"
    assert book["beginner_translation"].startswith("Read the first four chapters")
    assert book["freelancer_translation"] == ""


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


def test_search_scoped_to_chess_genre(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.get("/books/search", params={"q": "knight fork", "genre": "chess"})
    j = r.json()
    assert j["count"] == 1
    assert j["results"][0]["slug"] == "winning-chess-tactics"
    assert j["results"][0]["genre"] == "chess"
    assert j["results"][0]["tier"] == 1
    with _make_client(tmp_path) as c:
        r2 = c.get("/books/search", params={"q": "knight fork", "genre": "freelancer"})
    assert r2.json()["count"] == 0


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
        r = c.post(
            "/books/synthesize",
            json={"question": "how do I productize an AI automation service?", "generate": False},
        )
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["genre"] == "freelancer"
    assert j["question"].startswith("how do I productize")
    assert len(j["fragments"]) == 2
    assert j["fragments"][0]["slug"] == "the-e-myth-revisited"


def test_synthesize_chess_genre_only_uses_chess_books(tmp_path):
    with _make_client(tmp_path) as c:
        r = c.post(
            "/books/synthesize",
            json={
                "question": "I blunder every game and miss forks — what tactics should I drill?",
                "genre": "chess",
                "generate": False,
            },
        )
    assert r.status_code == 200
    j = r.json()
    assert j["genre"] == "chess"
    assert j["topic_tracks"] == ["tactics"]
    assert {f["slug"] for f in j["fragments"]} == {
        "winning-chess-tactics",
        "chess-fundamentals",
        "the-woodpecker-method",
    }
    assert j["fragments"][0]["beginner_translation"]


def test_synthesize_chess_at_500_caps_to_tier_1(tmp_path):
    """The bug: 'At 500, what should I drill' surfaced Tier-5 books. A low-rating
    hint must cap the pool to beginner tiers so the highest-signal fragments are
    Tier 1 (Bain/Polgar-style), never Tier 3+."""
    with _make_client(tmp_path) as c:
        r = c.post(
            "/books/synthesize",
            json={
                "question": "At 500, what should I drill first — and which book teaches it best?",
                "genre": "chess",
                "generate": False,
            },
        )
    assert r.status_code == 200
    j = r.json()
    assert j["level"] == 1
    slugs = {f["slug"] for f in j["fragments"]}
    # The tier-3 woodpecker must be excluded; only tier-1 books remain.
    assert "the-woodpecker-method" not in slugs
    assert slugs == {"winning-chess-tactics", "chess-fundamentals"}
    assert all((f.get("tier") or 99) == 1 for f in j["fragments"])


def test_synthesize_generates_answer_via_model(tmp_path, monkeypatch):
    """When generate=true, the service writes an actual answer through the
    analysis-cloud seam (mocked here) — the 'Ask the library' returns prose,
    not just fragments."""
    from swarm_os.services import books_service as bs

    async def fake_answer(self, question, genre, level, fragments):
        return "TIPS:\n1. Solve mate-in-one problems daily [Bain]"

    monkeypatch.setattr(bs.BooksService, "_write_answer", fake_answer)
    with _make_client(tmp_path) as c:
        r = c.post(
            "/books/synthesize",
            json={"question": "give me 100 chess tips", "genre": "chess"},
        )
    assert r.status_code == 200
    j = r.json()
    assert j["answer"].startswith("TIPS:")
    assert "Bain" in j["answer"]


def test_detect_chess_level():
    from swarm_os.services.books_service import _detect_chess_level

    assert _detect_chess_level("at 500 what should I drill") == 1
    assert _detect_chess_level("I am a beginner") == 1
    assert _detect_chess_level("for a 1200 player") == 3
    assert _detect_chess_level("how to play better") is None


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
