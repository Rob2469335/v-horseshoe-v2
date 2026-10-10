"""Tests for the operative S8 task registration (qwen_train.f2_registration).

Synthetic inputs only: a tiny union report plus a tiny acquired JSONL and its
PROVENANCE stub.  No task command is executed; test_cmd is inert text.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train.f2_registration import (
    PREDICATE,
    REGISTRATION_ID,
    SCHEMA_VERSION,
    all_pytest_node_ids,
    build_manifest,
    eligible,
    evaluator_compatible,
    is_pytest_command,
    python_confirmed,
)

_S8 = "S8_evidence_provenance"


class TestPredicates:
    @pytest.mark.parametrize("cmd,ok", [
        ("pytest -q", True),
        ("python -m pytest -rA .", True),
        ("py.test tests/", True),
        ("pytest -q --junitxml=x.xml", False),   # augment_test_command refuses it
        ("make test", False),
        ("npm test", False),
        ("", False),
        ("   ", False),
        (None, False),
    ])
    def test_is_pytest_command(self, cmd, ok):
        assert is_pytest_command(cmd) is ok

    @pytest.mark.parametrize("nodes,ok", [
        (["t/test_x.py::test_a"], True),
        (["t/test_x.py::TestC::test_a"], True),
        (["t/test_x.py::test_a[1]"], True),
        (["tests/x.yml::test_y"], True),          # well-formed, just not .py
        (["no_separator"], False),
        (["t/test_x.py::"], False),
        (["::test_a"], False),
        ([], False),
        (None, False),
    ])
    def test_all_pytest_node_ids(self, nodes, ok):
        assert all_pytest_node_ids(nodes) is ok

    def test_python_confirmed_requires_py_paths_on_both_fields(self):
        assert python_confirmed(["t/test_x.py::test_a"], ["t/test_x.py::test_b"]) is True
        assert python_confirmed(["tests/x.yml::test_y"], ["t/test_x.py::test_b"]) is False
        assert python_confirmed(["t/test_x.py::test_a"], ["IPython/p/test_x.txt::test_x"]) is False
        assert python_confirmed([], ["t/test_x.py::test_b"]) is False

    def test_evaluator_compatible_requires_all_three(self):
        assert evaluator_compatible("pytest -q", ["t/t.py::test_a"], ["t/t.py::test_b"]) is True
        assert evaluator_compatible("make test", ["t/t.py::test_a"], ["t/t.py::test_b"]) is False
        assert evaluator_compatible("pytest -q", ["nope"], ["t/t.py::test_b"]) is False

    def test_eligible_is_compatible_and_python_confirmed(self):
        assert eligible("pytest -q", ["t/t.py::a"], ["t/t.py::b"]) is True
        assert eligible("pytest -q", ["x.yml::a"], ["x.yml::b"]) is False   # compatible, not .py
        assert eligible("npm test", ["t/t.py::a"], ["t/t.py::b"]) is False


def _write_fixture(tmp_path: Path):
    d = tmp_path / "acq"
    d.mkdir()
    rows = [
        {  # eligible
            "instance_id": "a__ok-1", "created_at": "2025-06-01T00:00:00", "repo": "o/a",
            "test_cmds": ["pytest -q"],
            "FAIL_TO_PASS": ["t/test_x.py::test_a"], "PASS_TO_PASS": ["t/test_x.py::test_b"],
        },
        {  # evaluator-compatible but non-.py node paths
            "instance_id": "b__yml-1", "created_at": "2025-06-02T00:00:00", "repo": "o/b",
            "test_cmds": ["pytest -q"],
            "FAIL_TO_PASS": ["tests/x.yml::test_y"], "PASS_TO_PASS": ["tests/x.yml::test_z"],
        },
        {  # not evaluator-compatible (not a pytest command)
            "instance_id": "c__make-1", "created_at": "2025-06-03T00:00:00", "repo": "o/c",
            "test_cmds": ["make test"],
            "FAIL_TO_PASS": ["t/test_x.py::test_a"], "PASS_TO_PASS": ["t/test_x.py::test_b"],
        },
    ]
    p = d / "acquired.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (d / "PROVENANCE.json").write_text(
        json.dumps({"source_key": "swe-bench-live", "normalised_sha256": "a" * 64}),
        encoding="utf-8",
    )
    report = tmp_path / "union_report.json"
    report.write_text(json.dumps({
        "total_raw_rows": 3, "exclusions": 0, "total_accepted_after_union": 3,
        "screen_rejections": [
            {"instance_id": "a__ok-1", "source": "swe-bench-live", "failing_rules": [_S8]},
            {"instance_id": "b__yml-1", "source": "swe-bench-live", "failing_rules": [_S8]},
            {"instance_id": "c__make-1", "source": "swe-bench-live", "failing_rules": [_S8]},
        ],
    }), encoding="utf-8")
    return report, p


class TestBuildManifest:
    def test_reconciles_compatible_from_confirmed(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        manifest, ordered, excluded = build_manifest(
            union_report=report, acquired_paths=[acquired], generated_at="T"
        )
        c = manifest["counts"]
        assert c["metadata_eligible"] == 3
        assert c["evaluator_compatible"] == 2
        assert c["python_confirmed"] == 1
        assert c["registered"] == 1
        assert c["excluded"] == 0
        assert c["shortfall"] == {"not_evaluator_compatible": 1, "not_python_confirmed": 1}
        assert [r["instance_id"] for r in ordered] == ["a__ok-1"]

    def test_manifest_is_hash_only_and_records_rule_and_inputs(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        manifest, _, _ = build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")
        assert manifest["schema_version"] == SCHEMA_VERSION
        assert manifest["registration_id"] == REGISTRATION_ID
        assert manifest["predicate"] == PREDICATE
        assert manifest["ordering_rule"] == "created_at_desc_instance_id_asc_v1"
        assert manifest["inputs"]["union_report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
        for key in ("registration_content_sha256",):
            assert len(manifest[key]) == 64
        for value in manifest["artifacts_sha256"].values():
            assert len(value) == 64

    def test_registration_is_deterministic(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        a, _, _ = build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")
        b, _, _ = build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T2")
        assert a["registration_content_sha256"] == b["registration_content_sha256"]

    def test_digest_changes_when_registered_metadata_changes(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        base, _, _ = build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")
        text = acquired.read_text(encoding="utf-8").replace('"o/a"', '"o/other"')
        acquired.write_text(text, encoding="utf-8")
        after, _, _ = build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")
        assert base["registration_content_sha256"] != after["registration_content_sha256"]

    def test_empty_eligible_set_is_refused(self, tmp_path):
        report, acquired = _write_fixture(tmp_path)
        report.write_text(json.dumps({"screen_rejections": []}), encoding="utf-8")
        with pytest.raises(Exception, match="no metadata-eligible"):
            build_manifest(union_report=report, acquired_paths=[acquired], generated_at="T")
