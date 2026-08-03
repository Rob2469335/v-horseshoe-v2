"""Tests for the persistent LSP tool client.

A long-lived pylsp subprocess is kept warm per extension so agents don't pay a
per-call cold start. Tests cover the language-correct languageId mapping, the
pool's reuse/eviction behavior, and (when pylsp is installed) a real
diagnostics round-trip.
"""
from __future__ import annotations
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from swarm_os.capabilities import lsp_tool
from swarm_os.capabilities.lsp_tool import LSPToolHandler

_HAS_PYLSP = importlib.util.find_spec("pylsp") is not None


@pytest.fixture(autouse=True)
async def _clean_pool():
    await lsp_tool.close_all()
    yield
    await lsp_tool.close_all()


def test_language_id_mapping():
    assert lsp_tool.LANGUAGE_SERVERS[".py"]["languageId"] == "python"
    assert lsp_tool.LANGUAGE_SERVERS[".go"]["languageId"] == "go"
    assert lsp_tool.LANGUAGE_SERVERS[".rs"]["languageId"] == "rust"
    assert ".js" not in lsp_tool.LANGUAGE_SERVERS


def test_handler_rejects_bad_payload():
    res = asyncio.run(LSPToolHandler().execute([]))
    assert "error" in res


def test_handler_rejects_missing_file_path():
    res = asyncio.run(LSPToolHandler().execute({"operation": "diagnostics"}))
    assert res["error"] == "Missing 'file_path'"


def test_handler_rejects_missing_file():
    res = asyncio.run(
        LSPToolHandler().execute({"operation": "diagnostics", "file_path": "nope_does_not_exist.py"})
    )
    assert "File not found" in res["error"]


def test_handler_rejects_unsupported_extension(tmp_path):
    f = tmp_path / "foo.txt"
    f.write_text("hi")
    res = asyncio.run(LSPToolHandler().execute({"operation": "diagnostics", "file_path": str(f)}))
    assert res["error"] == "Unsupported language extension: .txt"


async def test_pool_reuses_warm_client(tmp_path):
    handler = LSPToolHandler()
    f = tmp_path / "mod_a.py"
    f.write_text("x = 1\n")

    await handler.execute({"operation": "diagnostics", "file_path": str(f)})
    first = lsp_tool._pool.get(".py")
    assert first is not None and first.alive

    await handler.execute({"operation": "diagnostics", "file_path": str(f)})
    assert lsp_tool._pool.get(".py") is first
    assert len(lsp_tool._pool) == 1


async def test_dead_client_is_evicted_and_respawned(tmp_path):
    handler = LSPToolHandler()
    f = tmp_path / "mod_b.py"
    f.write_text("x = 1\n")

    await handler.execute({"operation": "diagnostics", "file_path": str(f)})
    first = lsp_tool._pool[".py"]
    first.process.terminate()
    await asyncio.wait_for(first.process.wait(), timeout=5.0)

    res = await handler.execute({"operation": "diagnostics", "file_path": str(f)})
    assert "result" in res
    second = lsp_tool._pool[".py"]
    assert second is not first
    assert second.alive


@pytest.mark.skipif(not _HAS_PYLSP, reason="pylsp not installed")
async def test_real_diagnostics_round_trip(tmp_path):
    handler = LSPToolHandler()
    bad = tmp_path / "bad_mod.py"
    bad.write_text("def broken(:\n    pass\n")

    res = await handler.execute({"operation": "diagnostics", "file_path": str(bad)})
    assert "result" in res
    assert isinstance(res["result"], list)

    good = tmp_path / "good_mod.py"
    good.write_text("def ok():\n    return 1\n")
    res2 = await handler.execute({"operation": "diagnostics", "file_path": str(good)})
    assert "result" in res2
    # A fresh file should not inherit the previous file's syntax errors.
    assert not any("broken" in str(d.get("message", "")) for d in res2["result"])
