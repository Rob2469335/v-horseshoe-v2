"""Machine-checkable F2 population manifest (R1 / R8).

The readiness contract designates five candidate tasks in prose. This suite
pins the machine-checkable admission rules instead, and - importantly - proves
that a task MISSING its frozen relevant_file_set is REJECTED rather than
admitted with a default. That is the R8 gap the audit found: without such a
rule, a task with no endpoint would silently measure "no qualifying edit" and be
scored as a real observation.

No test is run, no service is started, no network is contacted.
"""
from __future__ import annotations

import atexit
import hashlib
import shutil
import tempfile
from pathlib import Path

import pytest

from qwen_train.f2_population import (
    MANIFEST_SCHEMA_VERSION,
    PopulationManifest,
    load_pool_rows,
    screen_entry,
    screen_pool_rows,
)

# S8 is a machine-verifiable contract: digests are compact identities, and
# admission requires VERIFIED evidence records. Verification now also REQUIRES a
# trusted artifact root and an evaluator authorization, so these fixtures create
# real artifacts in a shared root.
_EVALUATOR = "f2_base_gold_runner"
_EVALUATOR_VERSION = "v1"
AUTHORIZED = {_EVALUATOR: (_EVALUATOR_VERSION,)}

_EVIDENCE_ROOT = Path(tempfile.mkdtemp(prefix="f2_evidence_fixture_"))
atexit.register(shutil.rmtree, _EVIDENCE_ROOT, ignore_errors=True)


def _artifact(name: str, content: str, role: str):
    from qwen_train.f2_evidence import ArtifactRef

    data = content.encode("utf-8")
    (_EVIDENCE_ROOT / name).write_bytes(data)
    return ArtifactRef(
        name=name, digest=hashlib.sha256(data).hexdigest(), size_bytes=len(data), role=role
    )


def _evidence_pair(instance_id, repo, base_commit):
    from qwen_train.f2_evidence import (
        ROLE_RUN_LOG,
        ROLE_TEST_OUTPUT,
        build_evidence_record,
    )

    slug = instance_id.replace("/", "_").replace(".", "_")

    def rec(state, result):
        return build_evidence_record(
            instance_id=instance_id,
            repository=repo,
            base_commit=base_commit,
            execution_state_identity=state,
            execution_state_digest=hashlib.sha256(
                f"{state}|{instance_id}".encode()
            ).hexdigest(),
            test_command="pytest -q",
            environment_identity="python_base_310",
            evaluator_identity=_EVALUATOR,
            evaluator_version=_EVALUATOR_VERSION,
            execution_started_at="2026-10-04T00:00:00Z",
            execution_finished_at="2026-10-04T00:01:00Z",
            execution_result=result,
            test_output_artifact=_artifact(
                f"{slug}-{state}-test.out", f"{state} test output\n", ROLE_TEST_OUTPUT
            ),
            run_log_artifact=_artifact(
                f"{slug}-{state}-run.log", f"{state} run log\n", ROLE_RUN_LOG
            ),
        )

    return rec("base", "fail"), rec("gold", "pass")


_BASE_EV, _GOLD_EV = _evidence_pair(
    "pypa__twine-1066", "pypa/twine", "4a1fc064a7899872ee845df6a8810bb51a6845ac"
)

GOOD = dict(
    instance_id="pypa__twine-1066",
    repo="pypa/twine",
    base_commit="4a1fc064a7899872ee845df6a8810bb51a6845ac",
    test_cmd="pytest -q",
    fail_to_pass=["tests/test_package.py::test_x"],
    pass_to_pass=["tests/test_package.py::test_y"],
    relevant_file_set=["twine/package.py"],
    base_evidence_digest=_BASE_EV.evidence_record_digest,
    gold_evidence_digest=_GOLD_EV.evidence_record_digest,
    base_evidence=_BASE_EV,
    artifact_root=_EVIDENCE_ROOT,
    gold_evidence=_GOLD_EV,
    authorized_evaluators=AUTHORIZED,
)


def _entry(**over):
    kw = {**GOOD, **over}
    return screen_entry(**kw)


def _rule(entry, name) -> bool:
    return next(s.passed for s in entry.screens if s.rule == name)


class TestHappyPath:
    def test_a_complete_task_is_admitted(self):
        e = _entry()
        assert e.admitted is True
        assert all(s.passed for s in e.screens)

    def test_identity_hash_is_stable(self):
        assert _entry().identity_hash == _entry().identity_hash

    def test_identity_hash_changes_when_a_measured_field_changes(self):
        a = _entry().identity_hash
        b = _entry(base_commit="deadbeefdeadbeef").identity_hash
        assert a != b

    def test_identity_hash_ignores_mutable_annotations(self):
        """Reproving a task must not invalidate its frozen identity."""
        a = _entry().identity_hash
        b = _entry(usable=False).identity_hash
        assert a == b

    def test_every_screen_is_reported(self):
        names = {s.rule for s in _entry().screens}
        assert names == {
            "S1_identity",
            "S2_repo_and_base",
            "S3_fail_to_pass",
            "S4_pass_to_pass",
            "S5_test_cmd",
            "S6_relevant_file_set",
            "S7_source_not_test",
            "S8_evidence_provenance",
            "S9_probe_usable",
        }


class TestR8IsEnforcedNotDefaulted:
    def test_missing_relevant_file_set_is_rejected(self):
        e = _entry(relevant_file_set=None)
        assert _rule(e, "S6_relevant_file_set") is False
        assert e.admitted is False

    def test_empty_relevant_file_set_is_rejected(self):
        e = _entry(relevant_file_set=[])
        assert e.admitted is False

    def test_missing_evidence_records_are_rejected(self):
        """S8 gates on VERIFIED records; without them it fails closed."""
        e = _entry(base_evidence=None, gold_evidence=None)
        assert _rule(e, "S8_evidence_provenance") is False
        assert e.admitted is False

    def test_empty_evidence_identity_is_reported_but_not_gating(self):
        """A compact identity is a manifest convenience, not the admission gate.

        With no identity recorded the entry reports ``evidence_identity_recorded
        == False`` while verification still succeeds on the records themselves.
        Admission follows VERIFICATION, never string presence.
        """
        e = _entry(base_evidence_digest="", gold_evidence_digest="")
        assert e.to_dict()["evidence_identity_recorded"] is False
        assert e.evidence_verified is True
        assert e.admitted is True

    def test_test_file_in_relevant_set_is_rejected(self):
        """F1-OP-003 excluded the test file from the endpoint."""
        e = _entry(relevant_file_set=["tests/test_package.py"])
        assert _rule(e, "S7_source_not_test") is False
        assert e.admitted is False

    def test_declared_hash_mismatch_is_rejected(self):
        e = _entry(relevant_file_set_hash="0" * 64)
        assert _rule(e, "S6_relevant_file_set") is False


class TestOtherRules:
    @pytest.mark.parametrize(
        "over,rule",
        [
            ({"instance_id": ""}, "S1_identity"),
            ({"repo": "", "base_commit": "x"}, "S2_repo_and_base"),
            ({"base_commit": "abc"}, "S2_repo_and_base"),
            ({"fail_to_pass": []}, "S3_fail_to_pass"),
            ({"pass_to_pass": []}, "S4_pass_to_pass"),
            ({"test_cmd": "  "}, "S5_test_cmd"),
            ({"usable": False}, "S9_probe_usable"),
        ],
    )
    def test_each_rule_can_fail_independently(self, over, rule):
        e = _entry(**over)
        assert _rule(e, rule) is False
        assert e.admitted is False


class TestManifest:
    def test_duplicate_identity_is_refused(self):
        with pytest.raises(ValueError, match="duplicate"):
            PopulationManifest(entries=(_entry(), _entry())).verify()

    def test_empty_manifest_is_refused(self):
        with pytest.raises(ValueError, match="empty"):
            PopulationManifest(entries=()).verify()

    def test_verify_accepts_a_clean_manifest(self):
        second = _entry(instance_id="pallets__werkzeug-2583")
        PopulationManifest(entries=(_entry(), second)).verify()

    def test_summary_counts_admitted_and_failures(self):
        m = PopulationManifest(entries=(_entry(), _entry(relevant_file_set=None)))
        s = m.summary()
        assert s["total"] == 2
        assert s["admitted"] == 1
        assert s["failing_rules"]["S6_relevant_file_set"] == 1
        assert s["schema"] == MANIFEST_SCHEMA_VERSION

    def test_summary_reports_distinct_repositories(self):
        m = PopulationManifest(
            entries=(_entry(), _entry(instance_id="other__x-1", repo="other/repo"))
        )
        assert m.summary()["distinct_repositories"] == 2


class TestRealPoolScreening:
    """Screen the ACTUAL curriculum pool, read-only, and report the truth."""

    POOL = "qwen_train/curriculum/swe_pool.jsonl"

    def test_real_pool_is_read_and_screened(self):
        rows = load_pool_rows(self.POOL)
        assert len(rows) >= 1
        manifest = screen_pool_rows(rows)  # no relevant sets supplied => R8 fails
        manifest.verify()
        s = manifest.summary()
        # With no endpoint designated, EVERY task must be rejected on R8. This is
        # the honest current state and the test pins it, so it will FAIL loudly
        # if someone makes a task admissible without designating an endpoint.
        assert s["admitted"] == 0
        assert s["failing_rules"].get("S6_relevant_file_set") == len(rows)

    def test_digests_alone_do_not_admit_tasks(self):
        """S8 regression guard: a non-empty digest is NOT provenance.

        Previously this test asserted that supplying ``evidence_digests`` admitted
        tasks. That was the defect: string presence was treated as verified
        evidence. Under the machine-verifiable contract, digests are compact
        identities only, so every task must still fail S8.
        """
        rows = load_pool_rows(self.POOL)
        sets = {r["instance_id"]: ["pkg/mod.py"] for r in rows}
        digs = {r["instance_id"]: ("a" * 64, "b" * 64) for r in rows}
        manifest = screen_pool_rows(rows, relevant_file_sets=sets, evidence_digests=digs)
        manifest.verify()
        s = manifest.summary()
        assert s["admitted"] == 0
        assert s["failing_rules"]["S8_evidence_provenance"] == len(rows)
        # The identity was recorded, yet nothing was verified.
        assert s["evidence_identity_recorded"] == len(rows)
        assert s["evidence_verified"] == 0

    def test_verified_evidence_admits_tasks(self):
        """With endpoints AND verified base/gold evidence, admission follows DATA.

        Screening the real pool found that `pyqtgraph__pyqtgraph-1845` declares an
        EMPTY pass_to_pass set, so S4 (R3 needs something to regress) rejects it.
        That is a genuine gap in the corpus, not a screening bug, and this test
        pins it so it cannot be silently admitted later.
        """
        rows = load_pool_rows(self.POOL)
        sets = {r["instance_id"]: ["pkg/mod.py"] for r in rows}
        digs = {r["instance_id"]: ("a" * 64, "b" * 64) for r in rows}
        recs = {
            r["instance_id"]: _evidence_pair(
                r["instance_id"], r["repo"], r["base_commit"]
            )
            for r in rows
        }
        manifest = screen_pool_rows(
            rows,
            relevant_file_sets=sets,
            evidence_digests=digs,
            evidence_records=recs,
            artifact_root=_EVIDENCE_ROOT,
            authorized_evaluators=AUTHORIZED,
        )
        manifest.verify()
        summary = manifest.summary()
        rejected = [e.instance_id for e in manifest.entries if not e.admitted]
        assert rejected == ["pyqtgraph__pyqtgraph-1845"]
        assert summary["admitted"] == len(rows) - 1
        assert summary["failing_rules"]["S4_pass_to_pass"] == 1
        assert summary["evidence_verified"] == len(rows)

    def test_repr_encoded_list_fields_are_parsed(self):
        rows = [
            {
                "instance_id": "x__y-1",
                "repo": "x/y",
                "base_commit": "abcdef1234",
                "test_cmd": "pytest",
                "fail_to_pass": "['tests/t.py::a']",
                "pass_to_pass": "['tests/t.py::b']",
                "relevant_file_set": ["y/mod.py"],
                "usable": True,
            }
        ]
        manifest = screen_pool_rows(
            rows,
            relevant_file_sets={"x__y-1": ["y/mod.py"]},
            evidence_digests={"x__y-1": ("a" * 64, "b" * 64)},
            evidence_records={"x__y-1": _evidence_pair("x__y-1", "x/y", "abcdef1234")},
            artifact_root=_EVIDENCE_ROOT,
            authorized_evaluators=AUTHORIZED,
        )
        entry = manifest.entries[0]
        assert entry.fail_to_pass == ("tests/t.py::a",)
        assert entry.pass_to_pass == ("tests/t.py::b",)
        assert entry.admitted is True