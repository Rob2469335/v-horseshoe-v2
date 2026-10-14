"""Tests for the registration provenance verifier.

The defect these pin: ``registration_manifest.json`` is the git-tracked
``operative`` record of what is registered, and it carries an
``artifacts_sha256`` map over three git-ignored files. Nothing re-checked that
binding, and on 2026-10-14 all three digests were found to have drifted -- the
files on disk were already in the registrar's canonical serialization, so the
drift was a content change, not formatting. An S8 admission bound to that
manifest would have described files that no longer exist.

The verifier must therefore:

* agree when manifest and disk match;
* fail closed -- never raise, never return ``bound`` -- on any drift;
* notice a missing artifact and an unreadable one;
* compare the manifest's own counts against the files;
* never rewrite either side (it is a report, not a repair).

No test here touches the real registration; every fixture is built in
``tmp_path``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train.f2_registration_provenance import (
    verify_registration,
)


def _canon(payload) -> bytes:
    """Exactly the serialization qwen_train/f2_registration.py writes."""
    return (json.dumps(payload, indent=2, sort_keys=True,
                       ensure_ascii=False) + "\n").encode("utf-8")


def _write_fixture(root: Path, *, first_30, full, excluded, **manifest_over) -> Path:
    """Write a registration directory plus a matching manifest."""
    reg = root / "registration"
    reg.mkdir(parents=True, exist_ok=True)
    bodies = {
        "first_30.json": _canon(first_30),
        "full_ordered_metadata.json": _canon(full),
        "excluded.json": _canon(excluded),
    }
    for name, body in bodies.items():
        (reg / name).write_bytes(body)

    manifest = {
        "schema_version": "f2_registration_v1",
        "registration_id": "fixture_v1",
        "operative": True,
        "counts": {"registered": len(full), "excluded": len(excluded)},
        "artifacts_sha256": {
            "first_30": hashlib.sha256(bodies["first_30.json"]).hexdigest(),
            "full_ordered_metadata":
                hashlib.sha256(bodies["full_ordered_metadata.json"]).hexdigest(),
            "excluded": hashlib.sha256(bodies["excluded.json"]).hexdigest(),
        },
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
        assert failed[0].detail == "digest drift"

    def test_serialization_only_difference_is_still_a_drift(self, tmp_path):
        """The binding is over BYTES. A prettier-but-equivalent file is drift.

        This is what made the real finding ambiguous until it was checked: the
        on-disk files were already canonical, so the mismatch had to be content.
        """
        man = _write_fixture(tmp_path, first_30=FIRST, full=FULL, excluded=[])
        reg = tmp_path / "registration"
        (reg / "excluded.json").write_bytes(b"[]")  # no trailing newline
        report = verify_registration(reg, man)
        assert report.bound is False
        assert [c.key for c in report.checks if not c.match] == ["excluded"]

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


def test_real_registration_verdict_is_reported_not_repaired():
    """Smoke the live tree: whatever the verdict, nothing is rewritten.

    The real registration is currently UNBOUND (digest drift found 2026-10-14).
    This test asserts only that the verifier reaches a verdict without touching
    the files -- it must not pass by repairing, and must not fail by raising.
    """
    from qwen_train.f2_registration_provenance import DEFAULT_REGISTRATION_DIR, DEFAULT_MANIFEST_PATH
    if not Path(DEFAULT_MANIFEST_PATH).is_file():
        pytest.skip("registration manifest not present in this checkout")
    before = None
    if Path(DEFAULT_REGISTRATION_DIR).is_dir():
        before = {p.name: p.read_bytes()
                  for p in Path(DEFAULT_REGISTRATION_DIR).iterdir()}
    report = verify_registration()
    assert isinstance(report.bound, bool)
    assert len(report.checks) == 3
    if before is not None:
        after = {p.name: p.read_bytes()
                 for p in Path(DEFAULT_REGISTRATION_DIR).iterdir()}
        assert after == before
