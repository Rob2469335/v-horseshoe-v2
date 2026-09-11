"""Self-purging stale file-reference memory GC (2026-09-10).

A "File not found: <path>" reflection whose file has since been deleted keeps
re-entering agent context as if current (observed live: a stale
code_analysis_report.txt reflection fed fabricated findings -- models.py /
utils.py / Django -- into every codebase-analysis final). TTL pruning misses it
(the memory is recent); this GC drops it when the named path no longer exists.

All Qdrant calls are faked; no live service is touched.
"""

from __future__ import annotations

import pytest

import runtime_v2.services.memory_core as mc


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


@pytest.fixture
def fake_qdrant(monkeypatch):
    """Record scroll/delete calls and return a configurable point set."""
    calls = {"scroll": [], "delete": []}
    points = {"current": []}

    def _post(url, json=None, timeout=None, **kw):
        if url.endswith("/points/scroll"):
            calls["scroll"].append(json)
            return _FakeResp({"result": {"points": points["current"]}})
        if "/points/delete" in url:
            calls["delete"].append(json)
            return _FakeResp({"result": {"status": "completed"}, "status": "ok"}, 200)
        return _FakeResp({}, 404)

    monkeypatch.setattr(mc.requests, "post", _post)
    return calls, points


def test_stale_missing_file_memory_is_deleted(fake_qdrant):
    calls, points = fake_qdrant
    points["current"] = [
        {
            "id": "p1",
            "payload": {
                "fact": "Agent code_analyzer called filesystem which failed with "
                "File not found: code_analysis_report.txt. Strategy needs adjustment.",
                "category": "self_reflection",
            },
        }
    ]
    res = mc.prune_stale_file_memories()
    assert res["ok"] is True
    assert res["stale"] == 1
    assert res["deleted"] == 1
    assert calls["delete"] and calls["delete"][0]["points"] == ["p1"]


def test_memory_referencing_existing_file_is_kept(fake_qdrant):
    calls, points = fake_qdrant
    points["current"] = [
        {
            "id": "p2",
            "payload": {"fact": "File not found: AGENTS.md", "category": "self_reflection"},
        }
    ]
    res = mc.prune_stale_file_memories()
    assert res["stale"] == 0
    assert res["deleted"] == 0
    assert calls["delete"] == []


def test_generic_rule_mentioning_a_path_is_not_purged(fake_qdrant):
    calls, points = fake_qdrant
    # A lesson that merely names a file (no "File not found:" shape) must be kept
    # even if that filename happens not to exist on disk.
    points["current"] = [
        {
            "id": "p3",
            "payload": {
                "fact": "Before reading a file like nonexistent_thing.py, list the "
                "parent directory first.",
                "category": "self_reflection",
            },
        }
    ]
    res = mc.prune_stale_file_memories()
    assert res["stale"] == 0
    assert calls["delete"] == []


def test_dry_run_counts_but_deletes_nothing(fake_qdrant):
    calls, points = fake_qdrant
    points["current"] = [
        {
            "id": "p4",
            "payload": {"fact": "File not found: gone_forever.txt", "category": "self_reflection"},
        }
    ]
    res = mc.prune_stale_file_memories(dry_run=True)
    assert res["stale"] == 1
    assert res["deleted"] == 0
    assert res["dry_run"] is True
    assert calls["delete"] == []


def test_never_raises_on_qdrant_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(mc.requests, "post", _boom)
    res = mc.prune_stale_file_memories()
    assert res["ok"] is False
    assert "error" in res


def test_referenced_paths_missing_semantics():
    root = mc._project_root()
    # missing target -> True (stale)
    assert mc._referenced_paths_missing("File not found: code_analysis_report.txt", root) is True
    # real target -> False (keep)
    assert mc._referenced_paths_missing("File not found: AGENTS.md", root) is False
    # no not-found shape -> False (never purge a plain mention)
    assert mc._referenced_paths_missing("models.py line 56 has a bug", root) is False
