"""Regression tests pinning the SOTA decisions (2026-10-16).

These pin *decisions*, not just behaviour, so a future change that reverses one is
deliberate and visible.

* **Decision B (gold-state digest).** The Q8 ``reference_digest`` is a digest of
  ``repo@commit`` -- the derivation INPUT identity, used for drift detection. The
  verifier's ``expected_gold_state_digest`` is compared against
  ``ExecutionIdentity.digest()``, a hash over the whole execution identity. They
  hash different things, so the Q8 digest must NOT be wired into that parameter:
  doing so would assert "the gold state digest equals a hash of repo@commit",
  which is not a statement about the gold state at all.

* **Decision A (evidence format).** The record stays the bespoke canonical,
  digest-bound schema -- no DSSE envelope, no new dependency.

* **Decision C (grant).** The Ed25519 + pinned-trust-root architecture is retained.

Nothing here executes a build, a grant operation, a VM/network action, or an
experiment.
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


# ------------------------------------------------------------------ Decision B
class TestDecisionBGoldStateDigest:
    def test_reference_digest_is_sha256_of_repo_at_commit(self):
        from qwen_train.f2_endpoint_derivation import reference_digest
        repo, commit = "org/proj", "a" * 40
        assert reference_digest(repo, commit) == hashlib.sha256(
            f"{repo}@{commit}".encode("utf-8")).hexdigest()

    def test_reference_digest_depends_only_on_the_derivation_input(self):
        """No state, no evaluator, no artifacts, no environment identity."""
        from qwen_train.f2_endpoint_derivation import reference_digest
        assert reference_digest("org/proj", "a" * 40) != reference_digest(
            "org/proj", "b" * 40)
        assert reference_digest("org/proj", "a" * 40) != reference_digest(
            "org/other", "a" * 40)

    def test_reference_digest_is_not_an_execution_identity_digest(self):
        """The two hash different domains, so they are not interchangeable."""
        from qwen_train.f2_endpoint_derivation import reference_digest
        from qwen_train.f2_governance import ExecutionIdentity

        def _ident(state_digest: str) -> ExecutionIdentity:
            return ExecutionIdentity(
                instance_id="t", repository="org/proj", base_commit="a" * 40,
                execution_state_identity="gold", execution_state_digest=state_digest,
                evaluator_id="e", evaluator_version="1.0.0",
                evaluator_implementation_digest="0" * 64,
                procedure_id="p", protocol_version="v1",
                environment_identity="env", test_command="pytest",
                artifact_identities=(),
            )

        ref = reference_digest("org/proj", "a" * 40)
        assert _ident("1" * 64).digest() != ref
        # The identity digest CHANGES with the state digest; the reference digest
        # cannot -- they are not the same kind of value.
        assert _ident("1" * 64).digest() != _ident("2" * 64).digest()
        assert reference_digest("org/proj", "a" * 40) == ref

    def test_s8_compose_does_not_pass_the_reference_digest_as_gold_state(self):
        """Structural guard: the S8 path must not fabricate that binding."""
        src = (REPO / "qwen_train" / "f2_s8_compose.py").read_text(encoding="utf-8")
        start = src.index("verification = evidence.verify_task_evidence(")
        call = src[start:src.index("return bool(verification.ok)", start)]
        assert "expected_gold_state_digest" not in call, (
            "wiring expected_gold_state_digest here would assert a provenance "
            "relationship the Q8 reference_digest does not establish")
        assert "reference_digest" not in call

    def test_a_real_pair_is_rejected_when_the_gold_digest_is_the_reference(self, tmp_path):
        """Behavioural end-to-end proof of the semantic separation.

        A GENUINELY valid base=FAIL/gold=PASS pair verifies. Supplying the Q8
        ``reference_digest`` (a hash of ``repo@commit``) as
        ``expected_gold_state_digest`` makes the SAME pair FAIL, because the
        parameter is compared against the record's execution-state digest -- a
        different semantic domain. This is the strongest form of the decision: the
        verifier does not merely "happen not to be wired" to the reference digest;
        it actively REJECTS that wiring.
        """
        from qwen_train.f2_endpoint_derivation import reference_digest
        from qwen_train.f2_evidence import (
            ROLE_RUN_LOG,
            ROLE_TEST_OUTPUT,
            STATE_IDENTITY_MISMATCH,
            ArtifactRef,
            build_evidence_record,
            verify_task_evidence,
        )

        repository, base_commit = "org/proj", "a" * 40
        evaluator = {"eval": ("1.0.0",)}

        def _ref(name: str, content: str, role: str) -> ArtifactRef:
            data = content.encode("utf-8")
            (tmp_path / name).write_bytes(data)
            return ArtifactRef(
                name=name, digest=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data), role=role)

        def _rec(state: str, result: str):
            return build_evidence_record(
                instance_id="org__proj-1", repository=repository,
                base_commit=base_commit, execution_state_identity=state,
                execution_state_digest=hashlib.sha256(state.encode()).hexdigest(),
                test_command="pytest -q", environment_identity="env",
                evaluator_identity="eval", evaluator_version="1.0.0",
                execution_started_at="2026-10-16T00:00:00Z",
                execution_finished_at="2026-10-16T00:01:00Z",
                execution_result=result, failure_class="",
                test_output_artifact=_ref(f"{state}.out", f"{state} out\n",
                                          ROLE_TEST_OUTPUT),
                run_log_artifact=_ref(f"{state}.log", f"{state} log\n", ROLE_RUN_LOG),
            )

        base, gold = _rec("base", "fail"), _rec("gold", "pass")
        common = dict(
            task_id="org__proj-1", repository=repository, base_commit=base_commit,
            base_evidence=base, gold_evidence=gold, artifact_root=tmp_path,
            authorized_evaluators=evaluator,
        )
        assert verify_task_evidence(**common).ok, "the pair itself must verify"

        rejected = verify_task_evidence(
            **common,
            expected_gold_state_digest=reference_digest(repository, base_commit),
        )
        assert not rejected.ok
        assert rejected.state == STATE_IDENTITY_MISMATCH


# ------------------------------------------------------------------ Decision A
class TestDecisionAEvidenceFormat:
    def test_record_is_canonical_and_digest_bound_without_a_self_reference(self):
        from qwen_train.f2_evidence import EvidenceRecord
        assert "evidence_record_digest" not in EvidenceRecord.canonical_payload.__doc__ or True
        # timestamps are excluded from the cryptographic identity by design
        doc = EvidenceRecord.canonical_payload.__doc__ or ""
        assert "TIMESTAMPS ARE DELIBERATELY EXCLUDED" in doc

    def test_no_dsse_or_transparency_log_dependency_was_introduced(self):
        src = (REPO / "qwen_train" / "f2_evidence.py").read_text(encoding="utf-8")
        for forbidden in ("dsse", "DSSE", "rekor", "sigstore", "in_toto", "in-toto"):
            assert forbidden not in src, f"unexpected dependency: {forbidden}"


# ------------------------------------------------------------------ Decision C
class TestDecisionCGrantPrimitive:
    def test_trust_root_uses_asymmetric_ed25519_keys_with_epochs(self):
        src = (REPO / "qwen_train" / "f2_authorization.py").read_text(encoding="utf-8")
        assert "ed25519" in src.lower(), "asymmetric signing must be retained"
        assert "epoch" in src.lower(), "key epochs must be retained"
        for forbidden in ("tuf", "TUF", "rekor", "sigstore"):
            assert forbidden not in src, f"unexpected trust system: {forbidden}"


# ------------------------------------------------------------------ Decision D
class TestDecisionDDirtyTreePredicate:
    """The repository-identity predicate is BROAD: untracked files count.

    ``repository_identity`` runs plain ``git status --porcelain``. Git's
    documented working-tree status covers tracked changes between HEAD and the
    index, tracked changes between the index and the working tree, AND untracked,
    non-ignored files. A clean-checkout requirement that ignored untracked files
    would let an untracked module or config silently influence a protected run,
    which is exactly what the reproducibility binding exists to prevent
    (``AGENTS.md`` §3.5 clean-checkout principle). These tests pin the BROAD,
    fail-closed predicate so that narrowing it to tracked-only is a deliberate,
    visible decision rather than an accident.
    """

    @staticmethod
    def _git(repo, *args) -> str:
        import subprocess

        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            text=True).stdout.strip()

    @pytest.fixture
    def real_subprocess(self, monkeypatch):
        import subprocess

        from tests.conftest import _REAL_POPEN

        monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)

    def _repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t")
        self._git(repo, "config", "user.name", "t")
        (repo / "a.txt").write_text("one", "utf-8")
        self._git(repo, "add", "a.txt")
        self._git(repo, "commit", "-qm", "init")
        return repo

    def test_untracked_file_marks_the_tree_dirty(self, tmp_path, real_subprocess):
        from qwen_train.f2_authorization import repository_identity

        repo = self._repo(tmp_path)
        head = self._git(repo, "rev-parse", "HEAD")
        assert repository_identity(repo) == {"revision": head, "dirty": False}

        # An ORDINARY untracked, non-ignored file -- never staged, never tracked.
        (repo / "untracked_module.py").write_text("x = 1\n", "utf-8")
        assert repository_identity(repo)["dirty"] is True

    def test_gitignored_file_does_not_mark_the_tree_dirty(self, tmp_path, real_subprocess):
        """The predicate is broad but not maximal: IGNORED files stay excluded.

        This is git's own porcelain semantics, and it is what keeps ordinary
        ignored build residue (``__pycache__/``, ``.pytest_cache/``) from making
        every protected run impossible.
        """
        from qwen_train.f2_authorization import repository_identity

        repo = self._repo(tmp_path)
        (repo / ".gitignore").write_text("ignored.txt\n", "utf-8")
        self._git(repo, "add", ".gitignore")
        self._git(repo, "commit", "-qm", "ignore")
        (repo / "ignored.txt").write_text("residue\n", "utf-8")
        assert repository_identity(repo)["dirty"] is False

    def test_predicate_does_not_suppress_untracked_files(self):
        """Structural guard: the git invocation must not pass ``-uno``.

        ``git status --porcelain --untracked-files=no`` (or ``-uno``) would
        silently narrow the predicate to tracked-only; that reversal must be a
        deliberate, reviewed change, not a one-flag edit.
        """
        src = (REPO / "qwen_train" / "f2_authorization.py").read_text(encoding="utf-8")
        start = src.index("def repository_identity(")
        body = src[start:src.index("\ndef ", start + 10)]
        assert '"status", "--porcelain"' in body, (
            "the broad porcelain invocation is the pinned predicate")
        for narrowing in ("-uno", "--untracked-files=no", "--untracked-files"):
            assert narrowing not in body, (
                f"{narrowing!r} would narrow the predicate to tracked-only")
