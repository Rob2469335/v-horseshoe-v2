from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

from swarm_os.api.routes import get_memories


class _FakePoint:
    def __init__(self, payload):
        self.payload = payload


def _fake_vs(points_by_collection):
    vs = MagicMock()
    colls = [SimpleNamespace(name=name) for name in points_by_collection]
    client = AsyncMock()
    client.get_collections = AsyncMock(return_value=SimpleNamespace(collections=colls))

    async def scroll(collection_name=None, **kwargs):
        return ([_FakePoint(p) for p in points_by_collection[collection_name]], None)

    client.scroll = scroll
    vs.client = client
    return vs


async def test_memories_returns_sane_response_when_timestamp_none():
    """A payload with timestamp: None must NOT 500 — the /memories sort key
    crashes on float(None). The endpoint returns a sane response with the
    None-timestamped memory at the bottom."""
    vs = _fake_vs({"agent_memory": [{"fact": "old", "timestamp": None}]})
    with patch("swarm_os.services.vector_store.VectorStore", return_value=vs):
        result = await get_memories()
    assert result["status"] == "success"
    assert result["data"]["agent_memory"] == [{"fact": "old", "timestamp": None}]


async def test_memories_sorts_newest_first_mixed_timestamp_types():
    """Timestamps arrive as floats AND ISO strings across memory writers; the
    sort must handle both without crashing, newest first."""
    vs = _fake_vs(
        {
            "agent_memory": [
                {"fact": "iso_old", "timestamp": "2026-08-01T12:00:00+00:00"},
                {"fact": "float_new", "timestamp": 1786300000.0},
                {"fact": "none", "timestamp": None},
            ]
        }
    )
    with patch("swarm_os.services.vector_store.VectorStore", return_value=vs):
        result = await get_memories()
    order = [p["fact"] for p in result["data"]["agent_memory"]]
    assert order == ["float_new", "iso_old", "none"]


async def test_memories_handles_missing_and_garbage_timestamps():
    """Missing timestamp (default 0) and non-numeric garbage must not raise —
    both degrade to the bottom of the sort."""
    vs = _fake_vs(
        {
            "agent_memory": [
                {"fact": "garbage", "timestamp": "not-a-date"},
                {"fact": "missing"},
                {"fact": "num_new", "timestamp": 5000.0},
            ]
        }
    )
    with patch("swarm_os.services.vector_store.VectorStore", return_value=vs):
        result = await get_memories()
    order = [p["fact"] for p in result["data"]["agent_memory"]]
    assert order == ["num_new", "garbage", "missing"]


def test_memory_timestamp_except_handlers_are_parenthesized():
    """The `except (TypeError, ValueError):` comma form was a formatter-sweep
    regression (routes.py:926/:932). It still parses as a tuple on Python 3.14
    (so the /memories tests pass either way), but it is non-portable and
    non-idiomatic. Pin the AST to the parenthesized tuple so a future sweep
    that strips the parens fails loudly instead of passing on 3.14's tolerance.
    The 43f2622 ruff-format sweep re-introduced the comma form in
    deep_research.py / approval_registry.py (the ac6cef6 fix regressed), so
    scan every known-touched module, not just routes.py."""
    import ast

    scanned_files = [
        "swarm_os/api/routes.py",
        "swarm_os/services/deep_research.py",
        "swarm_os/services/approval_registry.py",
        "swarm_os/api/api_features.py",
        "swarm_os/capabilities/sandbox_repl.py",
        "swarm_os/healing/recovery_primitives.py",
        "swarm_os/lib/symbol_context.py",
        "swarm_os/services/evolution_daemon.py",
        "swarm_os/services/outcome_fitness.py",
    ]
    comma_forms = []
    for fname in scanned_files:
        src = open(fname, encoding="utf-8").read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                if (
                    isinstance(node.type, ast.Tuple)
                    and any(isinstance(e, ast.Name) for e in node.type.elts)
                    and node.type.elts
                ):
                    # A parenthesized tuple: ast keeps elts regardless of parens,
                    # so detect the SOURCE text of the handler type to prove parens.
                    lineno = node.lineno
                    lines = src.splitlines()
                    handler_line = lines[lineno - 1].strip()
                    if handler_line.startswith("except"):
                        comma_forms.append((fname, handler_line))
    offenders = [
        f"{fname}:{line}"
        for fname, line in comma_forms
        if ", " in line and not line.startswith("except (")
    ]
    assert offenders == [], f"comma-form except handlers present: {offenders}"


def test_memory_search_extracts_from_payload_level_v1():
    """REVERT-PROOF: VectorStore.search returns {id, score, payload}, so
    /memory/search must read text/sender/timestamp from hit['payload'] — the
    old hit.get('text'/'sender'/'timestamp') returned empties for every hit.
    (routes.memory_search is exercised via the real extraction path by
    monkeypatching VectorStore.search.)"""
    import swarm_os.api.routes as r

    captured = {}

    class FakeVS:
        def __init__(self, collection_name=None):
            captured["shard"] = collection_name

        async def search(self, query_vector=None, limit=8):
            return [
                {
                    "id": "p1",
                    "score": 0.9,
                    "payload": {
                        "text": "the actual memory text",
                        "sender": "researcher",
                        "timestamp": 1712300000,
                    },
                }
            ]

    def fake_get_embedding(q):
        return [0.1] * 8

    def fake_route(q):
        return ["general"]

    def fake_shard(name):
        return "agent_memory_general_v2"

    from unittest.mock import patch

    import runtime_v2.services.memory_core as mc
    import swarm_os.services.vector_store as vsmod

    with patch.object(mc, "get_embedding", fake_get_embedding), patch.object(
        mc, "_moe_route_shards", fake_route
    ), patch.object(mc, "_get_shard_name", fake_shard), patch.object(
        vsmod, "VectorStore", FakeVS
    ):
        import asyncio

        res = asyncio.run(r.memory_search("hello", 8))
    assert res["results"][0]["text"] == "the actual memory text", "text must come from payload"
    assert res["results"][0]["sender"] == "researcher", "sender must come from payload"
    assert res["results"][0]["timestamp"] == 1712300000, "timestamp must come from payload"


def test_memory_search_extracts_from_payload_level():
    """REVERT-PROOF: VectorStore.search returns {id, score, payload}; /memory/search
    must read text/sender/timestamp from hit['payload'] — the old hit.get('text')
    returned empties for every result (fields lived one level deeper)."""
    import asyncio
    from unittest.mock import patch

    import swarm_os.api.routes as r
    import runtime_v2.services.memory_core as mc
    import swarm_os.services.vector_store as vsmod

    def fake_get_embedding(q):
        return [0.1] * 8

    def fake_route(q):
        return ["general"]

    def fake_shard(name):
        return "agent_memory_general_v2"

    class FakeVS:
        def __init__(self, collection_name=None):
            pass

        async def search(self, query_vector=None, limit=8):
            return [
                {
                    "id": "p1",
                    "score": 0.9,
                    "payload": {
                        "text": "the actual memory text",
                        "sender": "researcher",
                        "timestamp": 1712300000,
                    },
                }
            ]

    with patch.object(mc, "get_embedding", fake_get_embedding), patch.object(
        mc, "_moe_route_shards", fake_route
    ), patch.object(mc, "_get_shard_name", fake_shard), patch.object(
        vsmod, "VectorStore", FakeVS
    ):
        res = asyncio.run(r.memory_search("hello", 8))
    assert res["results"][0]["text"] == "the actual memory text", "text must come from payload"
    assert res["results"][0]["sender"] == "researcher", "sender must come from payload"
    assert res["results"][0]["timestamp"] == 1712300000, "timestamp must come from payload"
def test_memories_paginates_all_points_not_truncated():
    """The /memories dump must page scroll() to completion instead of doing a
    single scroll(limit) — the old single-scroll silently dropped any memory
    beyond the limit when a collection exceeded it."""
    import asyncio

    pages = {
        "agent_memory": [
            ([_FakePoint({"fact": f"mem-{i}", "timestamp": i}) for i in range(0, 2)], "offset-1"),
            ([_FakePoint({"fact": f"mem-{i}", "timestamp": i}) for i in range(2, 4)], "offset-2"),
            ([_FakePoint({"fact": f"mem-{i}", "timestamp": i}) for i in range(4, 6)], None),
        ]
    }

    vs = MagicMock()
    client = AsyncMock()
    client.get_collections = AsyncMock(
        return_value=SimpleNamespace(collections=[SimpleNamespace(name="agent_memory")])
    )

    async def scroll(collection_name=None, **kwargs):
        calls = getattr(scroll, "_calls", 0)
        idx = min(calls, len(pages[collection_name]) - 1)
        scroll._calls = calls + 1
        return pages[collection_name][idx]

    client.scroll = scroll
    vs.client = client

    with patch("swarm_os.services.vector_store.VectorStore", return_value=vs):
        result = asyncio.run(get_memories())

    assert result["status"] == "success"
    facts = [p["fact"] for p in result["data"]["agent_memory"]]
    assert set(facts) == {f"mem-{i}" for i in range(6)}, f"got {facts}"
    assert len(facts) == 6, f"pagination truncated: {facts}"
    # descending timestamp order: mem-5 first, mem-0 last
    assert facts[0] == "mem-5" and facts[-1] == "mem-0"
