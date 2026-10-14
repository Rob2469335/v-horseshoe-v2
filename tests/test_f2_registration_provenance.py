"""Tests for the registration provenance verifier.

The binding these pin: ``registration_manifest.json`` is the git-tracked
``operative`` record of what is registered, and it carries an
``artifacts_sha256`` map over three git-ignored files. Which bytes it binds is
the subtle part -- ``registration_artifacts`` records the digest of the
**canonical row form** while the registrar **writes** those rows indented, so
the two serializations of identical content hash differently.

The first version of the verifier ignored that and hashed file bytes. On
2026-10-14 it therefore reported all three artifacts as drifted when a
record-level comparison against a deterministic recomputation from the pinned
inputs showed the rows were identical -- same instance_ids, same order, zero
differing fields -- and the registrar's own row digests matched the manifest
exactly. The registration was bound the whole time; the alarm was in the
verifier. A false alarm that blocks an admission pipeline is as damaging as a
missed one, so the corrected semantics are now pinned directly.

The verifier must therefore:

* agree when manifest and disk agree under the registrar's own digest;
* fail closed -- never raise, never return ``bound`` -- on a real ROW change;
* notice a missing artifact and an unreadable one;
* compare the manifest's own counts against the files;
* NOT mistake a serialization-only difference for drift;
* never rewrite either side (it is a report, not a repair).

No test here touches the real registration except the final one, which is
read-only and asserts the corrected verdict.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train.f2_population_build import jsonl_lines
from qwen_train.f2_registration_provenance import (
    verify_registration,
)

#: Repository root, for the live-input tests below.
REPO = Path(__file__).resolve().parents[1]



def _canon(payload) -> bytes:
    """Exactly the serialization qwen_train/f2_registration.py WRITES."""
    return (json.dumps(payload, indent=2, sort_keys=True,
                       ensure_ascii=False) + "\n").encode("utf-8")


def _row_digest(payload) -> str:
    """Exactly what registration_artifacts BINDS: the compact row form."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _write_fixture(root: Path, *, first_30, full, excluded, **manifest_over) -> Path:
    """Write a registration directory plus a matching manifest.

    The manifest records the registrar's ROW digest while the file is written
    indented -- the real shape of the binding, and the exact distinction whose
    absence produced the 2026-10-14 false alarm.
    """
    reg = root / "registration"
    reg.mkdir(parents=True, exist_ok=True)
    payloads = {
        "first_30": first_30,
        "full_ordered_metadata": full,
        "excluded": excluded,
    }
    # Manifest keys carry no ".json"; the on-disk names do.
    for key, payload in payloads.items():
        (reg / f"{key}.json").write_bytes(_canon(payload))

    manifest = {
        "schema_version": "f2_registration_v1",
        "registration_id": "fixture_v1",
        "operative": True,
        "counts": {"registered": len(full), "excluded": len(excluded)},
        "artifacts_sha256": {k: _row_digest(v) for k, v in payloads.items()},
    }
    manifest.update(manifest_over)
    path = root / "registration_manifest.json"
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return path


FULL = [{"instance_id": "a__b-1", "source": "s"},
        {"instance_id": "c__d-2", "source": "s"}]
FIRST = FULL[:1]


class TestBoundRegistration:
    def test_matching_manifest_is_bound(self, tmp_path):
        man = _write_fixture(tmp_path, first_30=FIRST, full=FULL, excluded=[])
        report = verify_registration(tmp_path / "registration", man)
        assert report.bound is True
        assert report.operative is True
        assert report.registration_id == "fixture_v1"
        assert len(report.checks) == 3
        assert all(c.match for c in report.checks)
        assert report.errors == []

    def test_report_serialises(self, tmp_path):
        man = _write_fixture(tmp_path, first_30=FIRST, full=FULL, excluded=[])
        payload = verify_registration(tmp_path / "registration", man).to_dict()
        assert json.loads(json.dumps(payload))["bound"] is True


class TestDriftIsDetected:
    """The 2026-10-14 finding, reproduced against a fixture."""

    def test_content_drift_on_one_artifact_fails_the_binding(self, tmp_path):
        man = _write_fixture(tmp_path, first_30=FIRST, full=FULL, excluded=[])
        reg = tmp_path / "registration"
        # Rewrite one artifact with DIFFERENT content, canonically serialized --
        # so only the bytes, not the formatting, differ.
        (reg / "first_30.json").write_bytes(_canon([{"instance_id": "z__z-9"}]))
        report = verify_registration(reg, man)
        assert report.bound is False
        failed = [c for c in report.checks if not c.match]
        assert [c.key for c in failed] == ["first_30"]
        assert failed[0].detail == "canonical row digest drift"

    def test_serialization_only_difference_is_NOT_drift(self, tmp_path):
        """The binding is over the registrar's row form, not the file bytes.

        Regression for the 2026-10-14 false alarm: the registrar writes rows
        indented while binding their compact digest, so hashing file bytes
        flagged a perfectly bound registration as drifted. Rewriting the file
        in a different-but-equivalent serialization must still verify.
        """
        man = _write_fixture(tmp_path, first_30=FIRST, full=FULL, excluded=[])
        reg = tmp_path / "registration"
        # Re-serialize one artifact differently: compact, and with a BOM-free
        # but differently ordered key layout. Content is identical.
        payload = json.loads((reg / "excluded.json").read_text(encoding="utf-8"))
        (reg / "excluded.json").write_bytes(json.dumps(payload).encode("utf-8"))
        report = verify_registration(reg, man)
        assert report.bound is True
        assert all(c.match for c in report.checks)

    def test_real_content_change_is_drift(self, tmp_path):
        """Only a change in the ROWS themselves is drift -- as it should be."""
        man = _write_fixture(tmp_path, first_30=FIRST, full=FULL, excluded=[])
        reg = tmp_path / "registration"
        (reg / "first_30.json").write_bytes(_canon([{"instance_id": "z__z-9"}]))
        report = verify_registration(reg, man)
        assert report.bound is False
        failed = [c for c in report.checks if not c.match]
        assert [c.key for c in failed] == ["first_30"]
        assert failed[0].detail == "canonical row digest drift"

    def test_every_artifact_drifting_fails(self, tmp_path):
        man = _write_fixture(tmp_path, first_30=FIRST, full=FULL, excluded=[])
        reg = tmp_path / "registration"
        for name in ("first_30.json", "full_ordered_metadata.json", "excluded.json"):
            (reg / name).write_bytes(_canon(["altered"]))
        report = verify_registration(reg, man)
        assert report.bound is False
        assert all(not c.match for c in report.checks)


class TestCountsAreCompared:
    def test_count_mismatch_fails_even_when_digests_match(self, tmp_path):
        """A manifest whose counts disagree with the files it names is not
        bound either -- the numbers are part of what was registered."""
        man = _write_fixture(
            tmp_path, first_30=FIRST, full=FULL, excluded=[],
            counts={"registered": 99, "excluded": 0},
        )
        report = verify_registration(tmp_path / "registration", man)
        assert report.bound is False
        failed = [c for c in report.count_checks if not c.match]
        assert [c.key for c in failed] == ["registered"]
        # the digests themselves still agree
        assert all(c.match for c in report.checks)

    def test_excluded_count_is_compared(self, tmp_path):
        man = _write_fixture(
            tmp_path, first_30=FIRST, full=FULL, excluded=[],
            counts={"registered": 2, "excluded": 7},
        )
        report = verify_registration(tmp_path / "registration", man)
        assert report.bound is False
        assert [c.key for c in report.count_checks if not c.match] == ["excluded"]


class TestFailClosed:
    def test_missing_manifest_reports_and_fails(self, tmp_path):
        report = verify_registration(tmp_path / "reg", tmp_path / "absent.json")
        assert report.bound is False
        assert report.errors

    def test_unreadable_manifest_reports_and_fails(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        report = verify_registration(tmp_path / "reg", bad)
        assert report.bound is False
        assert any("unreadable" in e for e in report.errors)

    def test_missing_artifact_fails(self, tmp_path):
        man = _write_fixture(tmp_path, first_30=FIRST, full=FULL, excluded=[])
        (tmp_path / "registration" / "excluded.json").unlink()
        report = verify_registration(tmp_path / "registration", man)
        assert report.bound is False
        failed = [c for c in report.checks if not c.match]
        assert [c.key for c in failed] == ["excluded"]
        assert failed[0].detail == "artifact missing"

    def test_non_list_artifact_is_counted_as_absent(self, tmp_path):
        man = _write_fixture(tmp_path, first_30=FIRST, full=FULL, excluded=[])
        (tmp_path / "registration" / "full_ordered_metadata.json").write_bytes(
            b'{"not": "a list"}')
        report = verify_registration(tmp_path / "registration", man)
        # the digest still matches what the manifest recorded, but the count
        # comparison sees a non-list and refuses to call it 2 entries.
        assert report.bound is False
        assert any(c.key == "registered" and not c.match for c in report.count_checks)


class TestItNeverRepairs:
    def test_verification_leaves_both_sides_byte_identical(self, tmp_path):
        man = _write_fixture(tmp_path, first_30=FIRST, full=FULL, excluded=[])
        reg = tmp_path / "registration"
        before = {p.name: p.read_bytes() for p in reg.iterdir()}
        manifest_before = man.read_bytes()

        verify_registration(reg, man)
        verify_registration(reg, man)  # idempotent

        after = {p.name: p.read_bytes() for p in reg.iterdir()}
        assert after == before
        assert man.read_bytes() == manifest_before


def test_real_registration_is_bound():
    """The live registration is BOUND -- the corrected 2026-10-14 verdict.

    The first version of this module hashed file bytes and reported all three
    artifacts as drifted. A record-level comparison against a deterministic
    recomputation from the pinned inputs showed the rows were identical -- same
    instance_ids, same order, zero differing fields -- and the registrar's own
    row digests matched the manifest exactly. The alarm was in the verifier, not
    in the registration.

    This test pins the corrected verdict so the false alarm cannot return, and
    asserts the verifier reaches it without rewriting anything.
    """
    from qwen_train.f2_registration_provenance import (
        DEFAULT_MANIFEST_PATH,
        DEFAULT_REGISTRATION_DIR,
    )
    if not Path(DEFAULT_MANIFEST_PATH).is_file():
        pytest.skip("registration manifest not present in this checkout")
    reg = Path(DEFAULT_REGISTRATION_DIR)
    if not reg.is_dir():
        pytest.skip("registration artifacts not present in this checkout")
    before = {p.name: p.read_bytes() for p in reg.iterdir()}

    report = verify_registration()

    assert report.bound is True, report.to_dict()
    assert all(c.match for c in report.checks), report.to_dict()
    assert all(c.match for c in report.count_checks), report.to_dict()
    assert report.registration_id == "evaluator_compat_python_confirmed_v1"
    assert report.operative is True
    # The verifier is a report, never a repair.
    assert {p.name: p.read_bytes() for p in reg.iterdir()} == before


class TestJsonlLineSplitting:
    """Regression for the defect that made the registration unreproducible.

    ``str.splitlines()`` splits on Unicode line boundaries. The real
    SWE-rebench-V2 acquisition carries two U+2029 PARAGRAPH SEPARATOR
    characters inside description fields, so splitting there tears those
    records in half and the parse dies with
    ``JSONDecodeError: Unterminated string``.

    The repair landed in f2_population_build on 2026-10-09, but the in-memory
    ``acquired_bytes`` path added by the 2026-10-11 TOCTOU fix called
    ``splitlines()`` again, so a registration could no longer be reproduced
    from its own pinned bytes. These tests pin the correct splitter so the
    defect cannot return a third time.
    """

    U2029 = " "
    U2028 = " "

    def test_splitlines_tears_a_record_the_helper_does_not(self):
        text = (
            '{"a": "x' + self.U2029 + 'y"}\n'
            '{"b": 1}\n'
        )
        assert len(text.splitlines()) == 3          # the bug
        assert len(jsonl_lines(text)) == 2          # the fix
        assert [json.loads(l) for l in jsonl_lines(text)] == [{"a": "x" + self.U2029 + "y"}, {"b": 1}]

    def test_every_documented_separator_is_handled(self):
        # Only three of str.splitlines()' separators are ALSO valid raw
        # characters inside a JSON string: NEL, LINE SEPARATOR and PARAGRAPH
        # SEPARATOR. U+000B/U+000C are below U+0020 and are invalid JSON
        # whatever the splitter does, so they are not splitting cases. The real
        # acquisition carries U+2029; NEL and U+2028 are the same failure mode.
        for sep in ("\x85", " ", " "):
            text = '{"a": "x' + sep + 'y"}\n{"b": 2}\n'
            rows = [json.loads(l) for l in jsonl_lines(text)]
            assert len(rows) == 2, f"separator U+{ord(sep):04X} tore a record"
            assert rows[0] == {"a": "x" + sep + "y"}
            # and the buggy splitter really does tear it, so the test is real
            assert len(text.splitlines()) == 3

    def test_crlf_files_still_parse(self):
        text = '{"a": 1}\r\n{"b": 2}\r\n'
        assert [json.loads(l) for l in jsonl_lines(text)] == [{"a": 1}, {"b": 2}]


    def test_blank_lines_are_dropped_without_loss(self):
        assert jsonl_lines('{"a":1}\n\n{"b":2}\n') == ['{"a":1}', '{"b":2}']
        assert jsonl_lines("") == []
        assert jsonl_lines("\n\n") == []

    def test_the_live_acquired_inputs_parse_with_the_helper(self):
        """The exact inputs that broke, parsed with the exact fix."""
        import sys
        sys.path.insert(0, r"C:\Users\rober\Projects\v-horseshoe-v2")
        from qwen_train.f2_population_build import _read_jsonl

        path = (REPO / "data/f2_population/upstream_swe_rebench_v2_10483de0"
                / "acquired.jsonl")
        if not path.is_file():
            pytest.skip("acquired input not present in this checkout")
        rows = _read_jsonl(path)
        # Every line parses: a torn record would raise JSONDecodeError before
        # this assertion, and the count matches the \n-delimited line count.
        assert len(rows) == 32079
        # At least one surviving record still carries a U+2029 verbatim, which
        # is only possible if the record was NOT split at that character.
        survivors = [r for r in rows
                     if self.U2029 in json.dumps(r, ensure_ascii=False)]
        assert len(survivors) >= 1


    def test_load_eligible_created_at_survives_the_separator(self):
        """The registration metadata path must not tear on U+2029."""
        import sys
        sys.path.insert(0, r"C:\Users\rober\Projects\v-horseshoe-v2")
        from qwen_train.f2_task_plan import load_eligible_created_at

        payload = (
            b'{"instance_id": "t__a-1", "created_at": "2025-01-01T00:00:00", '
            b'"test_cmd": "pytest -q", "repo": "t/a", "language": "python", '
            b'"FAIL_TO_PASS": ["t.py::t_a"], "PASS_TO_PASS": []}\n'
            b'{"instance_id": "t__b-2", "created_at": "2025-01-02T00:00:00", '
            b'"test_cmd": "pytest -q", "repo": "t/b", "language": "python", '
            b'"description": "sep' + " ".encode("utf-8") + b'arator", '
            b'"FAIL_TO_PASS": ["t.py::t_b"], "PASS_TO_PASS": []}\n'
        )
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "acquired.jsonl"
            p.write_bytes(payload)
            out = load_eligible_created_at(
                union_report=Path(td) / "union.json",
                acquired_paths=[p],
                eligible_ids={"t__a-1", "t__b-2"},
                acquired_bytes={p: payload},
            )
        assert set(out) == {"t__a-1", "t__b-2"}
