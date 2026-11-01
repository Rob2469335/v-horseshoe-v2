"""Regression tests for the shared (non-worktree-local) consumption ledger.

DEFECT CLOSED (owner decision **G1**, 2026-10-18).  ``DEFAULT_LEDGER_ROOT`` was
``Path(__file__).resolve().parents[1] / "data" / "f2_authz_ledger"`` -- derived
from the EXECUTING FILE and therefore **per worktree**.  Consumption is keyed on
the grant's record digest and created with ``O_CREAT | O_EXCL`` *inside that
root*, and no cross-root store is consulted.  The single-use guarantee therefore
held only *within one ledger root*: the consumed grant ``57935d17…f40e`` read as
UNCONSUMED from the main repository and from ``C:\\Users\\rober\\f2_exec\\rev-455c66ab``,
so it could have been consumed a second time.  ``GOVERNANCE GAP``, now closed by
one absolute path outside every worktree.
"""
from __future__ import annotations

import hashlib
import os
import pathlib

import pytest

from qwen_train.f2_authz_ledger import (
    DEFAULT_LEDGER_ROOT,
    AuthorizationLedger,
    ledger_root,
)

#: The one consumption record that existed when the root moved (non-secret id).
MIGRATED_RECORD_ID = "57935d171a486aff552c62d9cceefec1e08b9d0420f1ce94300c8e4778bdf40e"
MIGRATED_RECORD_SHA256 = "4b0e4f8613bb1787f0a1a021caad69d0b36a8be065f75ed51eb0576ea62e9dd3"
#: Its ORIGINAL location, which must survive the copy untouched.
SOURCE_ROOT = pathlib.Path(r"C:\Users\rober\f2_exec\rev-eb9d6c65\data\f2_authz_ledger")
#: The production root is an absolute path on every OS (Phase 1E): the owner's
#: fixed Windows path on Windows, a fixed absolute POSIX path elsewhere.  The
#: invariant asserted below is "one shared absolute path outside every
#: worktree", not one particular spelling.
SHARED_ROOT = (
    pathlib.Path(r"C:\Users\rober\F2_AUTHZ\ledger")
    if os.name == "nt"
    else pathlib.Path("/var/lib/F2_AUTHZ/ledger")
)
AUTHZ_DIR = pathlib.Path(r"C:\Users\rober\F2_AUTHZ")


class TestSharedRootResolution:
    def test_default_root_is_absolute(self):
        assert pathlib.Path(DEFAULT_LEDGER_ROOT).is_absolute(), DEFAULT_LEDGER_ROOT

    def test_default_root_is_not_inside_any_worktree_data_dir(self):
        """The whole point: the store must not be selected by the checkout."""
        assert pathlib.Path(DEFAULT_LEDGER_ROOT) == SHARED_ROOT
        assert not str(DEFAULT_LEDGER_ROOT).endswith("data\\f2_authz_ledger")
        assert "f2_exec" not in str(DEFAULT_LEDGER_ROOT)

    def test_ledger_root_helper_uses_the_shared_default(self):
        assert ledger_root() == SHARED_ROOT

    def test_explicit_root_still_injects(self, tmp_path):
        """Dependency injection is unchanged (tests must stay hermetic)."""
        assert ledger_root(tmp_path) == tmp_path

    def test_resolution_is_independent_of_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert ledger_root() == SHARED_ROOT


class TestMigratedRecord:
    @pytest.mark.skipif(not SHARED_ROOT.is_dir(), reason="shared ledger not present on this host")
    def test_the_migrated_record_is_present_and_byte_identical(self):
        f = SHARED_ROOT / f"{MIGRATED_RECORD_ID}.json"
        assert f.is_file(), f
        assert hashlib.sha256(f.read_bytes()).hexdigest() == MIGRATED_RECORD_SHA256

    @pytest.mark.skipif(not SHARED_ROOT.is_dir(), reason="shared ledger not present on this host")
    def test_the_migrated_grant_is_recognised_as_consumed(self):
        e = AuthorizationLedger().find(record_digest=MIGRATED_RECORD_ID)
        assert e is not None, "the migrated consumption record is not visible"
        assert e.get("record_digest") == MIGRATED_RECORD_ID

    @pytest.mark.skipif(not SOURCE_ROOT.is_dir(), reason="original ledger path absent on this host")
    def test_the_original_record_survived_the_copy_unchanged(self):
        """COPY, never move: the original keeps its bytes and its hash."""
        f = SOURCE_ROOT / f"{MIGRATED_RECORD_ID}.json"
        assert f.is_file()
        assert hashlib.sha256(f.read_bytes()).hexdigest() == MIGRATED_RECORD_SHA256
        assert f.stat().st_size == 660

    @pytest.mark.skipif(not AUTHZ_DIR.is_dir(), reason="authorization dir absent on this host")
    def test_existing_authorization_files_were_not_overwritten(self):
        for name in ("trust_root.json", "s8_001_grant.json", "TRUST_ROOT_FINGERPRINT.json"):
            assert (AUTHZ_DIR / name).is_file(), name


class TestDuplicateConsumptionRefused:
    """Disposable fixtures only: no real grant is ever consumed."""

    KW = dict(record_digest="f" * 64, stage="S8-001", operation="base_gold_production")

    def _consume(self, led, **over):
        return led.consume(**{
            **self.KW,
            "binding": {"candidate_manifest_digest": "x" * 64, "registration_sha256": "y" * 64},
            "binding_digest": "a" * 64,
            **over,
        })

    def test_first_consume_succeeds_and_second_is_refused(self, tmp_path):
        led = AuthorizationLedger(tmp_path)
        self._consume(led)
        with pytest.raises(Exception):
            self._consume(led)

    def test_a_substituted_binding_is_also_refused(self, tmp_path):
        """The unit is the GRANT, not the (grant, binding) pair."""
        led = AuthorizationLedger(tmp_path)
        self._consume(led)
        with pytest.raises(Exception):
            self._consume(led, binding_digest="b" * 64)

    def test_replay_is_refused_across_a_fresh_ledger_instance(self, tmp_path):
        """A new object over the same root still sees the consumption."""
        self._consume(AuthorizationLedger(tmp_path))
        with pytest.raises(Exception):
            self._consume(AuthorizationLedger(tmp_path))


class TestFailClosed:
    def test_inspection_does_not_create_or_consume(self, tmp_path):
        led = AuthorizationLedger(tmp_path)
        assert led.find(record_digest="a" * 64) is None
        assert list(tmp_path.glob("*.json")) == []      # inspection wrote nothing

    def test_an_unconsumed_digest_reads_as_unconsumed(self, tmp_path):
        assert AuthorizationLedger(tmp_path).find(record_digest="b" * 64) is None

    def test_a_malformed_consumption_record_fails_closed(self, tmp_path):
        (tmp_path / f"{'c' * 64}.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(Exception):
            AuthorizationLedger(tmp_path).find(record_digest="c" * 64)

    def test_a_missing_root_is_not_silently_treated_as_consumed(self, tmp_path):
        led = AuthorizationLedger(tmp_path / "absent")
        assert led.find(record_digest="d" * 64) is None
