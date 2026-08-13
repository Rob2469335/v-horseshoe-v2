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
    vs = _fake_vs({"agent_memory": [
        {"fact": "iso_old", "timestamp": "2026-08-01T12:00:00+00:00"},
        {"fact": "float_new", "timestamp": 1786300000.0},
        {"fact": "none", "timestamp": None},
    ]})
    with patch("swarm_os.services.vector_store.VectorStore", return_value=vs):
        result = await get_memories()
    order = [p["fact"] for p in result["data"]["agent_memory"]]
    assert order == ["float_new", "iso_old", "none"]


async def test_memories_handles_missing_and_garbage_timestamps():
    """Missing timestamp (default 0) and non-numeric garbage must not raise —
    both degrade to the bottom of the sort."""
    vs = _fake_vs({"agent_memory": [
        {"fact": "garbage", "timestamp": "not-a-date"},
        {"fact": "missing"},
        {"fact": "num_new", "timestamp": 5000.0},
    ]})
    with patch("swarm_os.services.vector_store.VectorStore", return_value=vs):
        result = await get_memories()
    order = [p["fact"] for p in result["data"]["agent_memory"]]
    assert order == ["num_new", "garbage", "missing"]


def test_memory_timestamp_except_handlers_are_parenthesized():
    """The `except TypeError, ValueError:` comma form was a formatter-sweep
    regression (routes.py:926/:932). It still parses as a tuple on Python 3.14
    (so the /memories tests pass either way), but it is non-portable and
    non-idiomatic. Pin the AST to the parenthesized tuple so a future sweep
    that strips the parens fails loudly instead of passing on 3.14's tolerance."""
    import ast

    src = open("swarm_os/api/routes.py", encoding="utf-8").read()
    tree = ast.parse(src)
    comma_forms = []
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
                    comma_forms.append(handler_line)
    offenders = [l for l in comma_forms if ", " in l and not l.startswith("except (")]
    assert offenders == [], f"comma-form except handlers present: {offenders}"
