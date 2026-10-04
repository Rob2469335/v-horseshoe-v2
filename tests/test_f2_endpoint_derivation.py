"""Governed Q8 endpoint derivation (reference modified-file set).

Builds REAL git repositories in tmp_path so the tests exercise the actual
discrimination logic, including the multi-child case that makes file counts
insufficient. No machine clone, network, service, or gold patch content is
touched.

Properties proved:
* read-only enforcement -- a write subcommand is refused;
* the declared test-patch set is the AUTHORITATIVE discriminator, and it is
  required to match EXACTLY;
* ambiguity fails closed rather than guessing;
* a missing/ambiguous match fails closed;
* the derived set is SOURCE-only (the reference test change is excluded);
* no raw reference commit id is persisted -- digest only;
* ``num_modified_files`` is a recorded diagnostic, not a gate, because the
  pool field carries inconsistent semantics;
* unsafe paths are refused via the shared readiness validator.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from qwen_train.f2_endpoint_derivation import (
    DERIVATION_METHOD,
    DerivationError,
    _git,
    derive_relevant_file_set,
    is_test_path,
    parse_test_patch_files,
    reference_digest,
)

DIFF_HEADER = "diff --git a/{p} b/{p}\n"


@pytest.fixture(scope="module", autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's session-scoped autouse ``subprocess.Popen`` mock.

    The derivation invokes real ``git`` subprocesses against real temporary
    repositories, which is the whole point of these tests. The conftest mock
    exists to stop tests spawning background servers; here it would silently
    replace git with a MagicMock. Same override convention as
    ``test_f2_orchestrator.py``.
    """
    yield


def _git_ok(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A repo with a base commit, one reference fix, and one decoy child."""
    r = tmp_path / "repo"
    r.mkdir()
    _git_ok(r, "init", "-q", "-b", "main")
    _git_ok(r, "config", "user.email", "t@example.invalid")
    _git_ok(r, "config", "user.name", "t")
    (r / "src").mkdir()
    (r / "tests").mkdir()
    (r / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (r / "tests" / "test_mod.py").write_text("def test_a(): pass\n", encoding="utf-8")
    (r / ".github").mkdir()
    (r / ".github" / "ci.yml").write_text("ci\n", encoding="utf-8")
    _git_ok(r, "add", "-A")
    _git_ok(r, "commit", "-qm", "base")
    base = _git_ok(r, "rev-parse", "HEAD")

    # The reference fix: touches one source + one test.
    (r / "src" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    (r / "tests" / "test_mod.py").write_text("def test_a(): pass\ndef test_b(): pass\n", encoding="utf-8")
    _git_ok(r, "add", "-A")
    _git_ok(r, "commit", "-qm", "reference fix")
    ref = _git_ok(r, "rev-parse", "HEAD")

    # Decoy: a sibling child of base touching a different single source file.
    # It must be on a REF so `rev-list --all` can see it -- an unreachable commit
    # would not be a candidate and would make the disambiguation test vacuous.
    _git_ok(r, "checkout", "-q", "-b", "decoy", base)
    (r / ".github" / "ci.yml").write_text("ci v2\n", encoding="utf-8")
    _git_ok(r, "add", "-A")
    _git_ok(r, "commit", "-qm", "decoy ci bump")
    decoy = _git_ok(r, "rev-parse", "HEAD")

    _git_ok(r, "checkout", "-q", "main")
    return {"repo": r, "base": base, "ref": ref, "decoy": decoy}


def _derive(repo, **kw):
    """Shared derivation invocation for every test class."""
    declared = kw.pop("declared", ["tests/test_mod.py"])
    return derive_relevant_file_set(
        instance_id="org__proj-1",
        repo="org/proj",
        clone_path=repo["repo"],
        base_commit=kw.pop("base_commit", repo["base"]),
        declared_test_patch_files=declared,
        **kw,
    )


class TestTestPathPartition:
    @pytest.mark.parametrize(
        "p", ["tests/test_a.py", "test/a.py", "testing/a.py", "a/test_x.py", "a_b_test.py"]
    )
    def test_test_paths_recognised(self, p):
        assert is_test_path(p) is True

    @pytest.mark.parametrize(
        "p", ["src/mod.py", "pkg/a.py", "README.md", "setup.cfg", "src/testing_utils.py"]
    )
    def test_source_paths_recognised(self, p):
        assert is_test_path(p) is False


class TestTestPatchParsing:
    def test_parses_headers(self):
        text = (
            "diff --git a/tests/test_mod.py b/tests/test_mod.py\n"
            "--- a/tests/test_mod.py\n+++ b/tests/test_mod.py\n"
        )
        assert parse_test_patch_files(text) == ("tests/test_mod.py",)

    def test_empty_patch_fails_closed(self):
        with pytest.raises(DerivationError, match="no `diff --git"):
            parse_test_patch_files("not a diff\n")


class TestReadOnlyEnforcement:
    def test_write_subcommand_is_refused(self, repo):
        for bad in (["reset", "--hard", "HEAD"], ["checkout", "x"], ["gc"]):
            with pytest.raises(DerivationError, match="non-read-only"):
                _git(repo["repo"], bad)

    def test_read_subcommands_are_allowed(self, repo):
        assert _git(repo["repo"], ["rev-parse", "HEAD"]).strip() == repo["ref"]
        assert "src/mod.py" in _git(repo["repo"], ["diff", "--name-only", repo["base"], repo["ref"]])


class TestDerivation:

    def test_selects_the_reference_commit_not_the_decoy(self, repo):
        res = _derive(repo)
        assert res.relevant_file_set == ("src/mod.py",)
        assert res.candidates_considered == 2  # the decoy is present and rejected

    def test_reference_test_file_is_excluded_from_the_endpoint(self, repo):
        res = _derive(repo)
        assert "tests/test_mod.py" not in res.relevant_file_set
        assert res.declared_test_files == ("tests/test_mod.py",)

    def test_digest_only_no_raw_commit_persisted(self, repo):
        payload = _derive(repo).to_dict()
        blob = repr(payload)
        assert repo["ref"] not in blob, "raw reference commit id must not be persisted"
        assert len(payload["reference_digest"]) == 64
        assert payload["reference_digest"] == reference_digest("org/proj", repo["ref"])

    def test_method_and_semantics_are_recorded(self, repo):
        payload = _derive(repo).to_dict()
        assert payload["derivation_method"] == DERIVATION_METHOD
        assert "NOT semantic ground truth" in payload["endpoint_semantics"]

    def test_hash_is_bound_to_the_set(self, repo):
        from runtime_v2.services.task_readiness import compute_relevant_file_set_hash

        res = _derive(repo)
        assert res.relevant_file_set_hash == compute_relevant_file_set_hash(
            res.relevant_file_set
        )

    def test_no_match_fails_closed(self, repo):
        with pytest.raises(DerivationError, match="no child of"):
            _derive(repo, declared=["tests/other.py"])

    def test_empty_declared_set_fails_closed(self, repo):
        with pytest.raises(DerivationError, match="empty"):
            _derive(repo, declared=[])

    def test_unknown_base_fails_closed(self, repo):
        with pytest.raises(DerivationError):
            _derive(repo, base_commit="0" * 40)

    def test_not_a_clone_fails_closed(self, tmp_path):
        with pytest.raises(DerivationError, match="not a git clone"):
            derive_relevant_file_set(
                instance_id="x",
                repo="r",
                clone_path=tmp_path,
                base_commit="a" * 40,
                declared_test_patch_files=["tests/a.py"],
            )


class TestAmbiguityFailsClosed:
    def test_two_matching_children_are_refused(self, repo):
        """Two commits touching the same test file must NOT be guessed apart."""
        r = repo["repo"]
        _git_ok(r, "checkout", "-q", repo["base"])
        (r / "src" / "mod.py").write_text("x = 99\n", encoding="utf-8")
        (r / "tests" / "test_mod.py").write_text(
            "def test_a(): pass\ndef test_b(): pass\ndef test_c(): pass\n", encoding="utf-8"
        )
        _git_ok(r, "add", "-A")
        _git_ok(r, "commit", "-qm", "second commit touching the same test file")
        with pytest.raises(DerivationError, match="AMBIGUOUS"):
            derive_relevant_file_set(
                instance_id="org__proj-1",
                repo="org/proj",
                clone_path=r,
                base_commit=repo["base"],
                declared_test_patch_files=["tests/test_mod.py"],
            )


class TestCountIsDiagnosticNotGate:
    def test_source_count_match_is_labelled(self, repo):
        res = _derive(repo, expected_source_file_count=1)
        assert res.count_agreement == "matches_source_count"
        assert res.declared_file_count == 1

    def test_total_count_match_is_labelled(self, repo):
        res = _derive(repo, expected_source_file_count=2)
        assert res.count_agreement == "matches_total_count"
        assert res.total_file_count == 2

    def test_mismatch_is_recorded_not_raised(self, repo):
        """A declared count that matches neither definition is a discrepancy to
        publish, not a reason to refuse a well-identified commit."""
        res = _derive(repo, expected_source_file_count=99)
        assert res.count_agreement == "mismatch"
        assert res.relevant_file_set == ("src/mod.py",)

    def test_absent_count_is_not_declared(self, repo):
        assert _derive(repo).count_agreement == "not_declared"


class TestEndpointMaterialisation:
    def test_result_materialises_a_hash_bound_endpoint(self, repo):
        from runtime_v2.services.task_readiness import TaskReadiness

        res = _derive(repo)
        tr = TaskReadiness(
            task_id="org__proj-1",
            base_commit=repo["base"],
            relevant_file_set=res.relevant_file_set,
        )
        assert tr.relevant_file_set == ("src/mod.py",)
        # The derivation method is an admissible, non-treatment-derived source.
        spec = res.to_endpoint(
            horizon_steps=12, derivation_evidence=("reference_modified_file_set_v1",)
        )
        assert spec.derivation_source == DERIVATION_METHOD
        assert spec.relevant_file_set_hash == res.relevant_file_set_hash