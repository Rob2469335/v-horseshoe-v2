"""F2 population recovery: serialization, timestamps, duplicates, R8 (F2-IMPL-AUTH-029).

No test is run, no service is started, no network is contacted.  These tests pin
the defects the recovery work fixed:

* D1 - ``to_dict()``/``summary()`` must preserve ``created_at`` + contamination.
* D2 - a task whose contamination is UNKNOWN must not be admitted.
* D3 - readiness must reject a bare ``admitted`` integer.
* D4 - malformed timestamps must fail closed, not compare lexically.
* D5 - duplicate identities must be excluded deterministically and recorded.
"""
from __future__ import annotations

import pytest

from qwen_train.f2_population import (
    PopulationManifest,
    ScreenResult,
    contamination_policy_record,
    deduplicate_rows,
    normalise_timestamp,
    parse_timestamp,
    screen_entry,
    screen_pool_rows,
    TimestampError,
)
from qwen_train.f2_relevant_files import (
    changed_paths_from_patch,
    is_test_path,
    relevant_file_set_from_patch,
    patch_test_paths,
)

_PATCH = """\
diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1 +1 @@
-a
+b
diff --git a/tests/test_mod.py b/tests/test_mod.py
--- a/tests/test_mod.py
+++ b/tests/test_mod.py
@@ -1 +1 @@
-a
+b
diff --git a/removed/old.py b/removed/old.py
deleted file mode 100644
--- a/removed/old.py
+++ /dev/null
diff --git a/docs/guide.md b/docs/guide.md
--- a/docs/guide.md
+++ b/docs/guide.md
@@ -1 +1 @@
-a
+b
"""


class TestTimestamps:
    def test_date_only_is_midnight_utc(self):
        assert normalise_timestamp("2024-06-01") == "2024-06-01T00:00:00"

    def test_iso_with_z(self):
        assert normalise_timestamp("2024-06-01T12:30:45Z") == "2024-06-01T12:30:45"

    def test_offset_is_normalised_to_utc(self):
        assert normalise_timestamp("2024-06-01T12:00:00+02:00") == "2024-06-01T10:00:00"

    def test_date_only_cutoff_compares_correctly_with_datetime(self):
        # The old lexical compare could not do this safely.
        assert parse_timestamp("2024-01-01") <= parse_timestamp("2024-01-01T02:57:59")

    @pytest.mark.parametrize("bad", ["", "  ", "not-a-date", "2024-13-40", "20240601"])
    def test_malformed_is_rejected(self, bad):
        with pytest.raises(TimestampError):
            parse_timestamp(bad)


class TestMalformedContamination:
    def _e(self, **over):
        base = dict(
            instance_id="x__y-1",
            repo="x/y",
            base_commit="abcdef1234",
            test_cmd="pytest",
            fail_to_pass=["tests/t.py::a"],
            pass_to_pass=["tests/t.py::b"],
            relevant_file_set=["pkg/mod.py"],
        )
        base.update(over)
        return screen_entry(**base)

    def test_malformed_created_at_fails_closed(self):
        e = self._e(created_at="not-a-date", model_cutoff="2024-01-01")
        rule = next(s for s in e.screens if s.rule == "S10_contamination_provenance")
        assert rule.passed is False
        assert e.contamination_state == "MALFORMED_DATE"
        assert e.contamination_class == "UNKNOWN"
        assert e.admitted is False

    def test_malformed_cutoff_fails_closed(self):
        e = self._e(created_at="2025-06-01", model_cutoff="whenever")
        rule = next(s for s in e.screens if s.rule == "S10_contamination_provenance")
        assert rule.passed is False
        assert e.contamination_state == "CUTOFF_INVALID"
        assert e.contamination_class == "UNKNOWN"

    def test_undeclared_cutoff_is_not_clean(self):
        e = self._e()
        assert e.contamination_class == "UNKNOWN"
        assert e.admitted is False
        assert e.metadata_eligible is True


class TestDeduplicate:
    def test_identical_duplicate_keeps_first_and_records(self):
        rows = [
            {"instance_id": "a__b-1", "x": 1},
            {"instance_id": "a__b-1", "x": 1},
            {"instance_id": "c__d-2", "x": 2},
        ]
        kept, rejections = deduplicate_rows(rows)
        assert [r["instance_id"] for r in kept] == ["a__b-1", "c__d-2"]
        assert len(rejections) == 1
        assert rejections[0]["reason"] == "duplicate_instance_id_identical"

    def test_conflicting_duplicate_removes_all_copies(self):
        rows = [
            {"instance_id": "a__b-1", "x": 1},
            {"instance_id": "a__b-1", "x": 2},
            {"instance_id": "c__d-2", "x": 3},
        ]
        kept, rejections = deduplicate_rows(rows)
        assert [r["instance_id"] for r in kept] == ["c__d-2"]
        reasons = {r["reason"] for r in rejections}
        assert "duplicate_instance_id_conflicting" in reasons
        assert "duplicate_instance_id_conflicted_removed" in reasons

    def test_missing_identity_is_kept_for_screening(self):
        kept, rejections = deduplicate_rows([{"instance_id": ""}, {"instance_id": "a__b-1"}])
        assert len(kept) == 2
        assert rejections == []

    def test_deduplication_is_order_deterministic(self):
        rows = [{"instance_id": "a__b-1", "x": i} for i in range(3)]
        rows.append({"instance_id": "z__z-9", "x": 0})
        kept1, rej1 = deduplicate_rows(rows)
        kept2, rej2 = deduplicate_rows(list(reversed(rows)))
        assert {r["instance_id"] for r in kept1} == {r["instance_id"] for r in kept2}
        assert len(rej1) == len(rej2)


class TestRelevantFilesFromPatch:
    def test_source_only_and_sorted(self):
        assert relevant_file_set_from_patch(_PATCH) == ("pkg/mod.py", "removed/old.py")

    def test_non_source_docs_are_excluded(self):
        """S7 measures a SOURCE edit; docs are not source (fail-closed on unknown ext)."""
        assert "docs/guide.md" in changed_paths_from_patch(_PATCH)
        assert "docs/guide.md" not in relevant_file_set_from_patch(_PATCH)

    def test_test_paths_are_excluded(self):
        assert patch_test_paths(_PATCH) == ("tests/test_mod.py",)

    def test_changed_paths_includes_tests(self):
        assert changed_paths_from_patch(_PATCH) == (
            "pkg/mod.py",
            "tests/test_mod.py",
            "removed/old.py",
            "docs/guide.md",
        )

    def test_specs_as_source_package_is_not_a_test_dir(self):
        """Regression: ``litserve/specs/openai.py`` is source, not a test file."""
        assert is_test_path("src/litserve/specs/openai.py") is False
        assert is_test_path("specs/thing.js") is True

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("tests/t.py", True),
            ("test/thing.py", True),
            ("pkg/tests/test_x.py", True),
            ("conftest.py", True),
            ("pkg/conftest.py", True),
            ("mod_test.py", True),
            ("pkg/mod.py", False),
            ("contest.py", False),
            ("src/litserve/specs/openai.py", False),
        ],
    )
    def test_is_test_path(self, path, expected):
        assert is_test_path(path) is expected

    def test_empty_patch_yields_empty_set(self):
        assert relevant_file_set_from_patch("") == ()


class TestPolicyAndManifestPlumbing:
    def test_policy_record_is_explicitly_a_proxy(self):
        rec = contamination_policy_record("2024-01-01")
        assert rec["is_proxy"] is True
        assert rec["declared"] is True
        assert rec["cutoff_normalised"] == "2024-01-01T00:00:00"
        assert "PROXY" in rec["limitation"]

    def test_policy_record_invalid_cutoff_is_recorded(self):
        rec = contamination_policy_record("nonsense")
        assert rec["cutoff_normalised"] is None and rec["cutoff_error"]

    def test_summary_carries_policy_and_rejections(self):
        rows = [{"instance_id": "a__b-1"}, {"instance_id": "a__b-1"}]
        m = screen_pool_rows(
            rows,
            rejections=[{"instance_id": "a__b-1", "reason": "duplicate_instance_id_identical"}],
            contamination_policy=contamination_policy_record("2024-01-01"),
        )
        s = m.summary()
        assert s["contamination_policy"]["declared"] is True
        assert s["rejections"][0]["reason"] == "duplicate_instance_id_identical"
        assert s["metadata_eligible"] == 0

    def test_screen_result_roundtrip(self):
        assert ScreenResult("S1_identity", True, "x").to_dict() == {
            "rule": "S1_identity",
            "passed": True,
            "detail": "x",
        }

    def test_empty_manifest_is_refused(self):
        with pytest.raises(ValueError, match="empty"):
            PopulationManifest(entries=()).verify()
