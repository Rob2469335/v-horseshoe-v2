"""F2 population union: source adapters, cross-source dedupe, determinism.

F2-IMPL-AUTH-031.  No network, no execution: these tests write tiny synthetic
``acquired.jsonl`` files to a temp dir and drive the real union builder.
"""
from __future__ import annotations

import json
from pathlib import Path

from qwen_train.f2_population_build import (
    _coerce_created_at,
    build_union,
    source_to_screen_row,
)

_PATCH = "diff --git a/pkg/m.py b/pkg/m.py\n--- a/pkg/m.py\n+++ b/pkg/m.py\n"


def _row(instance_id="o__a-1", **over):
    base = {
        "instance_id": instance_id,
        "repo": "o/a",
        "base_commit": "a" * 40,
        "FAIL_TO_PASS": ["tests/t.py::x"],
        "PASS_TO_PASS": ["tests/t.py::y"],
        "patch": _PATCH,
    }
    base.update(over)
    return base


def _write(tmp_path: Path, name: str, rows) -> Path:
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def _write_verbatim(tmp_path: Path, name: str, rows) -> Path:
    """Write JSONL with literal (unescaped) unicode, as a real acquisition may."""
    p = tmp_path / name
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    return p


class TestUnicodeJsonlParsing:
    """The real acquisition failed with 'Unterminated string' because
    ``str.splitlines()`` splits on U+2028/U+2029/NEL inside a JSON string."""

    def test_unicode_line_separators_do_not_split_a_record(self, tmp_path):
        weird = "line\u2028sep\u2029and\x85end"
        p = _write_verbatim(
            tmp_path,
            "u.jsonl",
            [
                _row("o__a-1", FAIL_TO_PASS=[weird], patch=_PATCH),
                _row("o__b-2", FAIL_TO_PASS=["ok"], patch=_PATCH),
            ],
        )
        man, _ = build_union([("swe-rebench-v2", p)])
        assert sorted(e.instance_id for e in man.entries) == ["o__a-1", "o__b-2"]
        entry = next(e for e in man.entries if e.instance_id == "o__a-1")
        assert entry.fail_to_pass == (weird,)  # content round-trips exactly

    def test_reader_splits_on_newline_only(self, tmp_path):
        from qwen_train.f2_population_build import _read_jsonl

        weird = "a\u2028b\u2029c\x85d"
        p = _write_verbatim(tmp_path, "u2.jsonl", [{"instance_id": "x", "v": weird}])
        rows = _read_jsonl(p)
        assert len(rows) == 1
        assert rows[0]["v"] == weird


class TestAdapters:
    def test_live_test_cmd_from_test_cmds_list(self):
        sr = source_to_screen_row("swe-bench-live", _row(test_cmds=["make test", "pytest -rA"]))
        assert sr["test_cmd"] == "make test && pytest -rA"

    def test_rebench_test_cmd_from_install_config(self):
        sr = source_to_screen_row(
            "swe-rebench-v2", _row(install_config={"test_cmd": "npm run test:unit"})
        )
        assert sr["test_cmd"] == "npm run test:unit"

    def test_fail_to_pass_preserved_verbatim(self):
        sr = source_to_screen_row("swe-rebench-v2", _row(FAIL_TO_PASS=["suite x"]))
        assert sr["fail_to_pass"] == ["suite x"]

    def test_created_at_space_format(self):
        assert _coerce_created_at("2025-06-01 12:00:00") == "2025-06-01T12:00:00"

    def test_created_at_epoch_ms(self):
        assert _coerce_created_at(1717243200000) == "2024-06-01T12:00:00"

    def test_created_at_already_iso(self):
        assert _coerce_created_at("2025-06-01T12:00:00") == "2025-06-01T12:00:00"


class TestUnion:
    def test_identical_cross_source_duplicate_keeps_priority_source(self, tmp_path):
        live = _write(tmp_path, "live.jsonl", [_row(test_cmds=["pytest -q"], created_at="2025-06-01T00:00:00")])
        reb = _write(
            tmp_path, "reb.jsonl",
            [_row(install_config={"test_cmd": "pytest -q"}, created_at="2025-06-01 00:00:00")],
        )
        man, rec = build_union([("swe-rebench-v2", reb), ("swe-bench-live", live)])
        assert len(man.entries) == 1  # one canonical identity
        assert rec["total_accepted_after_union"] == 1
        reasons = [e["reason"] for e in rec["pre_screen_exclusions"]]
        assert "duplicate_identical_cross_source" in reasons
        assert rec["sources"][0]["source_key"] == "swe-rebench-v2"

    def test_conflicting_cross_source_keeps_priority_source(self, tmp_path):
        live = _write(
            tmp_path, "live.jsonl",
            [_row("o__a-1", test_cmds=["pytest -q"], created_at="2025-06-01T00:00:00")],
        )
        reb = _write(
            tmp_path, "reb.jsonl",
            [_row("o__a-1", install_config={"test_cmd": "npm test"}, created_at="2025-06-01 00:00:00")],
        )
        man, rec = build_union([("swe-bench-live", live), ("swe-rebench-v2", reb)])
        # Same underlying issue (repo+PR): one unit, priority source kept, recorded.
        assert [e.instance_id for e in man.entries] == ["o__a-1"]
        assert man.entries[0].test_cmd == "pytest -q"
        assert "o__a-1" in rec["conflicting_ids"]
        assert rec["conflicting_ids_all_copies_removed"] == []

    def test_within_source_conflict_excludes_all(self, tmp_path):
        live = _write(
            tmp_path, "live.jsonl",
            [
                _row("o__a-1", test_cmds=["pytest -q"], created_at="2025-06-01T00:00:00"),
                _row("o__a-1", test_cmds=["pytest -rA"], created_at="2025-06-01T00:00:00"),
                _row("o__c-3", test_cmds=["pytest -q"], created_at="2025-06-01T00:00:00"),
            ],
        )
        man, rec = build_union([("swe-bench-live", live)])
        assert [e.instance_id for e in man.entries] == ["o__c-3"]
        assert rec["conflicting_ids_all_copies_removed"] == ["o__a-1"]

    def test_distinct_ids_across_sources_are_kept(self, tmp_path):
        live = _write(tmp_path, "live.jsonl", [_row("o__a-1", test_cmds=["pytest -q"])])
        reb = _write(
            tmp_path, "reb.jsonl",
            [_row("o__b-2", install_config={"test_cmd": "npm test"}, created_at="2025-06-01 00:00:00")],
        )
        man, rec = build_union([("swe-bench-live", live), ("swe-rebench-v2", reb)])
        assert len(man.entries) == 2
        assert rec["total_accepted_after_union"] == 2

    def test_union_is_order_independent(self, tmp_path):
        live = _write(tmp_path, "live.jsonl", [_row("o__a-1", test_cmds=["pytest -q"])])
        reb = _write(
            tmp_path, "reb.jsonl",
            [_row("o__b-2", install_config={"test_cmd": "npm test"}, created_at="2025-06-01 00:00:00")],
        )
        a, _ = build_union([("swe-bench-live", live), ("swe-rebench-v2", reb)])
        b, _ = build_union([("swe-rebench-v2", reb), ("swe-bench-live", live)])
        assert sorted(e.instance_id for e in a.entries) == sorted(e.instance_id for e in b.entries)

    def test_language_is_recorded(self, tmp_path):
        reb = _write(
            tmp_path, "reb.jsonl",
            [_row(language="go", install_config={"test_cmd": "go test"}, created_at="2025-06-01 00:00:00")],
        )
        _, rec = build_union([("swe-rebench-v2", reb)])
        assert rec["language_counts"].get("go") == 1

    def test_missing_fail_to_pass_is_rejected_not_admitted(self, tmp_path):
        reb = _write(
            tmp_path, "reb.jsonl",
            [_row(FAIL_TO_PASS=[], install_config={"test_cmd": "go test"}, created_at="2025-06-01 00:00:00")],
        )
        man, rec = build_union([("swe-rebench-v2", reb)])
        assert len(man.admitted) == 0
        assert "S3_fail_to_pass" in rec["screen_rejections"][0]["failing_rules"]

    def test_metadata_eligible_still_requires_s8(self, tmp_path):
        """A schema-complete task is metadata-eligible but NOT admitted (S8 missing)."""
        reb = _write(
            tmp_path, "reb.jsonl",
            [_row(install_config={"test_cmd": "go test"}, created_at="2025-06-01 00:00:00")],
        )
        man, _ = build_union([("swe-rebench-v2", reb)])
        assert len(man.metadata_eligible) == 1
        assert len(man.admitted) == 0
