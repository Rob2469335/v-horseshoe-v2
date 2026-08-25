"""Tests for move 5 — reranker/distillation learning upgrades.

5a: precedent retrieval (check_for_past_mistakes) now routes candidates through
    the cross-encoder reranker. The RERANK SCORE IS THE PRIMARY RANK; recency
    decay is a tiebreak and confidence*decay is a MINIMUM FILTER — never a
    co-equal multiplier. The case that matters: dense-nearest-but-wrong must
    yield to the reranker's judgment, or the fix does nothing.
5b: _distill's cloud chain is 2026-aligned (OpenCode Go flash leads; stale
    `deepseek-chat` string gone) and the model_variability short-circuit stays.
"""

from types import SimpleNamespace

from swarm_os.services import reflection_loop as rl
from runtime_v2.services import memory_core as mc


# ── 5a: rerank is primary ───────────────────────────────────────────────────
def _make_service(monkeypatch, client, rerank_fn):
    svc = rl.ReflectionService.__new__(rl.ReflectionService)
    svc._init_task = None
    svc._ensured = True
    svc.collection = "ReflexionMemory"
    svc.embedder = SimpleNamespace(
        embed=lambda *a, **k: __import__("asyncio").sleep(0) or [0.1] * 768
    )

    # embed is async; use a coroutine-returning stub
    async def _embed(*a, **k):
        return [0.1] * 768

    svc.embedder.embed = _embed
    svc.client = client
    if rerank_fn is not None:
        monkeypatch.setattr(mc, "rerank_memories", rerank_fn)
    return svc


def test_rerank_score_is_primary_rank_when_reranker_runs(monkeypatch):
    """The case the fix exists for: the dense-nearest candidate is WRONG, but the
    reranker correctly judges a different candidate as most relevant. The rerank
    score must win — not the dense score (which would defeat the whole fix)."""
    # Three stored rules. Dense-nearest = rule A (wrong); reranker says rule B.
    dense_rules = {
        "a": {
            "id": "a",
            "payload": {
                "correction": "list parent dir first",
                "confidence": 0.9,
                "timestamp": 2000000000,
            },
        },
        "b": {
            "id": "b",
            "payload": {
                "correction": "never reuse a stale cache key",
                "confidence": 0.9,
                "timestamp": 2000000000,
            },
        },
        "c": {
            "id": "c",
            "payload": {
                "correction": "reset cooldowns",
                "confidence": 0.9,
                "timestamp": 2000000000,
            },
        },
    }
    dense_scores = {"a": 0.95, "b": 0.60, "c": 0.55}  # dense-nearest = A
    rerank_scores = {"a": 0.20, "b": 0.90, "c": 0.10}  # reranker says B

    class _FakeClient:
        async def query_points(self, **kwargs):
            pts = []
            for key, score in dense_scores.items():
                pts.append(
                    SimpleNamespace(
                        id=key, score=score, payload=dense_rules[key]["payload"]
                    )
                )
            return SimpleNamespace(points=pts)

    svc = _make_service(monkeypatch, _FakeClient(), None)

    def _fake_rerank(query, memories):
        out = []
        for mem in memories:
            mid = mem.get("id")
            out.append(
                {
                    "id": mid,
                    "score": rerank_scores.get(mid, 0.0),
                    "fact": (mem.get("payload") or {}).get("correction", ""),
                }
            )
        out.sort(key=lambda x: x["score"], reverse=True)
        return out

    monkeypatch.setattr(mc, "rerank_memories", _fake_rerank)

    import asyncio

    hint = asyncio.run(
        svc.check_for_past_mistakes("the cache keeps returning stale data")
    )
    # The reranker's top pick (B: stale cache key) must surface — NOT dense-nearest A.
    assert "stale cache key" in hint


def test_rerank_outage_falls_back_to_dense(monkeypatch):
    """Reranker outage (raises) must not crash — fall back to dense ordering."""

    class _FakeClient:
        async def query_points(self, **kwargs):
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id="a",
                        score=0.9,
                        payload={
                            "correction": "rule A",
                            "confidence": 0.9,
                            "timestamp": 2000000000,
                        },
                    ),
                ]
            )

    svc = _make_service(monkeypatch, _FakeClient(), None)

    def _boom(query, memories):
        raise RuntimeError("reranker down")

    monkeypatch.setattr(mc, "rerank_memories", _boom)

    import asyncio

    hint = asyncio.run(svc.check_for_past_mistakes("any query"))
    assert "rule A" in hint  # graceful dense fallback


def test_rerank_respects_confidence_decay_minimum_filter(monkeypatch):
    """Confidence*decay is a MINIMUM FILTER: a rerank-top candidate whose rule has
    decayed below the floor must NOT surface (even if the reranker ranks it #1)."""
    stale_payload = {
        "correction": "old stale rule",
        "confidence": 0.2,
        "timestamp": 0,
    }  # decayed
    fresh_payload = {
        "correction": "fresh rule",
        "confidence": 0.9,
        "timestamp": 2000000000,
    }

    class _FakeClient:
        async def query_points(self, **kwargs):
            return SimpleNamespace(
                points=[
                    SimpleNamespace(id="stale", score=0.99, payload=stale_payload),
                    SimpleNamespace(id="fresh", score=0.8, payload=fresh_payload),
                ]
            )

    svc = _make_service(monkeypatch, _FakeClient(), None)

    def _fake_rerank(query, memories):
        return [
            {"id": "stale", "score": 0.99, "fact": "old stale rule"},
            {"id": "fresh", "score": 0.5, "fact": "fresh rule"},
        ]

    monkeypatch.setattr(mc, "rerank_memories", _fake_rerank)

    import asyncio

    hint = asyncio.run(svc.check_for_past_mistakes("q"))
    # The decayed rerank-#1 candidate is filtered out; the fresh one surfaces.
    assert "fresh rule" in hint
    assert "old stale rule" not in hint


# ── 5b: _distill chain alignment ────────────────────────────────────────────
def test_distill_uses_deepseek_v4_flash_when_openai_key(monkeypatch):
    """The stale `deepseek-chat` alias is gone; with OPENAI_API_KEY set, the
    distiller's chain leads with `deepseek-v4-flash` (funded OpenCode Go)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    try:
        import asyncio

        captured = {"models": []}

        async def _fake_acompletion(**cfg):
            captured["models"].append(cfg.get("model"))
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="<reflection>captured</reflection>",
                            reasoning_content=None,
                        )
                    )
                ],
            )

        monkeypatch.setattr(rl, "acompletion", _fake_acompletion)

        asyncio.run(rl._distill("content", fix_class="prompt_sensitivity"))
        assert captured["models"], "expected at least one distiller attempt"
        assert captured["models"][0] == "deepseek-v4-flash"
    finally:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_distill_model_variability_still_short_circuits(monkeypatch):
    """Regression guard: fix_class == model_variability must skip the LLM call."""
    import asyncio

    called = {"n": 0}

    async def _fake_acompletion(**cfg):
        called["n"] += 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="<reflection>x</reflection>", reasoning_content=None
                    )
                )
            ],
        )

    monkeypatch.setattr(rl, "acompletion", _fake_acompletion)
    out = asyncio.run(rl._distill("content", fix_class="model_variability"))
    assert out == ""
    assert called["n"] == 0


def _distill_capture_first_model(monkeypatch, env):
    """Common harness: set exactly `env`, capture the first distiller attempt's
    model id (all other provider keys stripped so only one branch is active)."""
    import asyncio

    for key in (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "NVIDIA_API_KEY",
        "NVIDIA_NIM_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(env[0], env[1])

    captured = {"models": []}

    async def _fake_acompletion(**cfg):
        captured["models"].append(cfg.get("model"))
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="<reflection>captured</reflection>",
                        reasoning_content=None,
                    )
                )
            ],
        )

    monkeypatch.setattr(rl, "acompletion", _fake_acompletion)
    asyncio.run(rl._distill("content", fix_class="prompt_sensitivity"))
    assert captured["models"], f"expected a distiller attempt for {env[0]}"
    return captured["models"][0]


def test_distill_openrouter_attempt_uses_free_ox_alpha(monkeypatch):
    """The OpenRouter distiller attempt MUST use an openrouter/-prefixed model.
    Pre-fix it used CLOUD_MODEL ('openai/deepseek-v4-flash') which litellm
    routed to the OpenCode Go endpoint — re-hitting the already-dead provider
    instead of ever reaching OpenRouter. The slot is stealth/ox-alpha because
    OpenRouter hosts NO free deepseek-v4-flash variant."""
    model = _distill_capture_first_model(
        monkeypatch, ("OPENROUTER_API_KEY", "sk-or-test")
    )
    assert model == "openrouter/stealth/ox-alpha"


def test_distill_nvidia_attempt_uses_surviving_v4_flash_0731(monkeypatch):
    """The NVIDIA distiller attempt must use the -0731 build: the plain
    deepseek-ai/deepseek-v4-flash reached end-of-life on NIM (HTTP 410 Gone,
    2026-08-07) and only -0731 is still served (verified live 2026-08-23).
    Also pins the litellm auth contract: nvidia_nim reads NVIDIA_NIM_API_KEY,
    so _distill must mirror it from NVIDIA_API_KEY or the call 401s even with
    a valid key (verified live 2026-08-23)."""
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    model = _distill_capture_first_model(monkeypatch, ("NVIDIA_API_KEY", "nvapi-test"))
    assert model == "nvidia_nim/deepseek-ai/deepseek-v4-flash-0731"
    import os

    assert os.environ["NVIDIA_NIM_API_KEY"] == "nvapi-test"


def test_init_memory_qdrant_no_duplicate_put_under_concurrency(monkeypatch):
    """TOCTOU regression: concurrent callers missing the verified-shards cache
    must not all issue a collection PUT (400 'Already Exists' for all but one).

    Pre-fix: the _verified_shards check and the GET+PUT creation sequence ran
    with no lock, so burst-launched callers raced and each sent its own PUT.
    Post-fix: one _verified_shards_lock covers check + create, so exactly one
    PUT fires and the rest read the freshly-verified cache entry."""
    import threading
    import runtime_v2.services.memory_core as _mc

    put_calls = {"n": 0}
    put_lock = threading.Lock()
    start_gate = threading.Barrier(6)

    class _Resp:
        status_code = 200

        def json(self):
            return {}

    class _Missing:
        status_code = 404

    monkeypatch.setattr(_mc, "_verified_shards", set())
    monkeypatch.setattr(_mc, "_verified_shards_lock", threading.Lock(), raising=False)
    monkeypatch.setattr(_mc, "_get_embedding_dimension", lambda: 768)

    def _fake_get(url, timeout=5.0):
        # Sleep releases the GIL so concurrent callers genuinely interleave;
        # pre-fix they all see the empty verified-shards cache and all PUT.
        import time

        time.sleep(0.05)
        return _Missing()

    def _fake_put(url, json, timeout=10.0):
        with put_lock:
            put_calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(_mc.requests, "get", _fake_get)
    monkeypatch.setattr(_mc.requests, "put", _fake_put)

    results = [None] * 6
    threads = []
    for i in range(6):

        def _run(i=i):
            start_gate.wait(timeout=5)
            results[i] = _mc.init_memory_qdrant("test")

        t = threading.Thread(target=_run)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    assert all(results), f"all callers must succeed, got {results}"
    assert put_calls["n"] == 1, f"PUT fired {put_calls['n']} times, expected 1"
    assert "agent_memory_test_v2" in _mc._verified_shards
