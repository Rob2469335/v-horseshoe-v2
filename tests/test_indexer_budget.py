"""Tests for the codebase-indexer token-budget splitter.

The embed server context varies across boots (512-2048 ctx seen); a chunk too
large for the model's context makes /v1/embeddings reject the WHOLE batch
(model_create 400), silently dropping every file in it (only 592/1737 files
landed). `_fit_token_budget()` derives a character budget from the model's LIVE
n_ctx (so it always fits the server) and must never let a chunk exceed it.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _mock_models(monkeypatch, n_ctx: int):
    """Point the embed-context probe at a fixed live n_ctx."""
    import runtime_v2.services.indexer as idx

    fake = MagicMock()
    fake.json = MagicMock(return_value={"data": [{"meta": {"n_ctx": n_ctx}}]})
    monkeypatch.setattr(idx.requests, "get", lambda *a, **k: fake)


def test_budget_tracks_live_n_ctx(monkeypatch):
    import runtime_v2.services.indexer as idx
    from runtime_v2.services.indexer import _embed_context_budget_chars

    # The live n_ctx drives the budget, but it is CAPPED at _MAX_BUDGET_CHARS
    # (a single embed request must stay in the fast CPU regime; gte-modernbert
    # boots at n_ctx=8192 -> raw formula ~24k chars, too slow per char) and
    # FLOORED at 1024 (never below a workable chunk).
    _mock_models(monkeypatch, 2048)
    assert _embed_context_budget_chars() == min(
        int(2048 * 3.5 * 0.85), idx._MAX_BUDGET_CHARS
    )
    _mock_models(monkeypatch, 512)
    assert _embed_context_budget_chars() == int(512 * 3.5 * 0.85)
    _mock_models(monkeypatch, 100)
    assert _embed_context_budget_chars() == 1024  # floor: never below 1024
    _mock_models(monkeypatch, 0)
    assert _embed_context_budget_chars() == 1800  # fallback on probe failure


def test_budget_fallback_on_probe_error(monkeypatch):
    import runtime_v2.services.indexer as idx

    def boom(*a, **k):
        raise RuntimeError("server down")

    monkeypatch.setattr(idx.requests, "get", boom)
    assert idx._embed_context_budget_chars() == idx.TOKEN_BUDGET_CHARS


def test_fit_token_budget_leaves_short_text_unchanged(monkeypatch):
    import runtime_v2.services.indexer as idx

    _mock_models(monkeypatch, 2048)
    short = "def foo(): pass"
    assert idx._fit_token_budget(short) == short


def test_fit_token_budget_chops_to_live_context(monkeypatch):
    import runtime_v2.services.indexer as idx

    _mock_models(monkeypatch, 512)
    budget = idx._embed_context_budget_chars()
    long = " ".join(["word" + str(i) for i in range(10000)])
    fitted = idx._fit_token_budget(long)
    assert len(fitted) <= budget
    assert fitted.startswith("word0")
    # A 2048-ctx server allows ~4x bigger chunks -> fewer, faster embeds.
    _mock_models(monkeypatch, 2048)
    big_budget = idx._embed_context_budget_chars()
    assert big_budget > budget
    assert len(idx._fit_token_budget(long)) <= big_budget


def test_fit_token_budget_preserves_whole_words(monkeypatch):
    import runtime_v2.services.indexer as idx

    _mock_models(monkeypatch, 512)
    long = "alpha beta gamma delta epsilon zeta"
    fitted = idx._fit_token_budget(long)
    assert fitted == fitted.rstrip()
    words = fitted.split()
    # Every kept word appears verbatim in the original (no mid-word cut).
    for w in words:
        assert w in long


def test_fit_token_budget_handles_empty():
    import runtime_v2.services.indexer as idx

    assert idx._fit_token_budget("") == ""
    assert idx._fit_token_budget(None) is None


def test_get_embeddings_budgets_each_text(monkeypatch):
    """get_embeddings must never send a text larger than the live token budget
    to the embed endpoint."""
    import runtime_v2.services.indexer as idx

    _mock_models(monkeypatch, 512)
    budget = idx._embed_context_budget_chars()
    sent = []

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"embedding": [0.1] * 768} for _ in sent]}

    def fake_post(*args, **kwargs):
        sent.extend(kwargs["json"]["input"])
        return FakeResp()

    monkeypatch.setattr(idx.requests, "post", fake_post)

    long = " ".join(["word" + str(i) for i in range(10000)])
    out = idx.get_embeddings([long])
    assert out is not None
    assert all(len(t) <= budget for t in sent)
