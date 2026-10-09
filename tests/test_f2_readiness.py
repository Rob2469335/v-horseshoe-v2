"""F2 execution-prerequisite readiness checker (F2-IMPL-AUTH-020).

Proves the checker (a) fails closed and names every blocker when evidence is
absent, (b) validates supplied evidence well-formedness, (c) never infers
no-egress from configuration, and (d) turns READY only when ALL prerequisites
are supplied. Uses explicit test-scoped fixtures; no real evidence.
"""
from __future__ import annotations

import atexit
import hashlib
import pathlib
import shutil
import tempfile
from pathlib import Path

import pytest

from qwen_train.f2_arm_primitives import render_block_from_records
from qwen_train.f2_readiness import (
    FROZEN_MIN_PAIRS,
    READINESS_ITEMS,
    evaluate_f2_readiness,
)
from runtime_v2.services.f2_freeze import LessonEntry, freeze_artifact

RULE = "Prefer pathlib over os.path in twine/package.py."
_HEX = "a" * 64


def _t_manifest():
    lessons = (
        LessonEntry(
            lesson_id="L1",
            lesson_hash=hashlib.sha256(RULE.encode()).hexdigest(),
            position=1,
            rule_text=RULE,
        ),
    )
    return freeze_artifact(
        rendered_artifact=render_block_from_records(lessons),
        arm="T",
        ordered_lessons=lessons,
        lesson_l_id="L1",
        lesson_l_hash=lessons[0].lesson_hash,
        freeze_timestamp=1.0,
    )


def _x_manifest():
    return freeze_artifact(
        rendered_artifact="# tool schema only (no lesson block)",
        arm="X",
        freeze_timestamp=1.0,
    )


def tmp_path_for(case):
    """A per-call temporary directory (unique per test invocation)."""
    import tempfile
    return pathlib.Path(tempfile.mkdtemp(prefix="f2rd_"))


def _all_env(tmp_path=None):
    # The AUTHORITATIVE evaluator now exists, so the fixture names it rather
    # than the placeholders that stood in before it did.
    from qwen_train.f2_evaluator import identity_fields as _ident

    _f = _ident()
    env = {
        "SWARM_DISTILLER_MODEL": "qwen3.5-4b-distiller",
        "SWARM_DISTILLER_WEIGHTS_DIGEST": _HEX,
        "SWARM_F2_EVALUATOR_ID": _f["evaluator_id"],
        "SWARM_F2_EVALUATOR_VERSION": _f["version"],
        "SWARM_F2_EVALUATOR_PROCEDURE": _f["procedure_id"],
        "SWARM_F2_ARTIFACT_ROOT": str(
            (tmp_path / "store") if tmp_path is not None else Path("C:/trusted/f2_store")
        ),
        "SWARM_F2_ARTIFACT_RETENTION_DAYS": "365",
        "SWARM_RECEIPT_KEY": "test-scoped-receipt-key",
        # Governed bundle emission must be ENABLED and the evaluator's report
        # destination CONFIGURED, otherwise the arm produces no evidence chain.
        "SWARM_F2_EMIT_BUNDLE": "1",
        "SWARM_F2_TASK_OUTCOME_REPORT": "C:\\trusted\\f2_store\\task_outcome.json",
    }
    if tmp_path is not None:
        impl = tmp_path / "evaluator_impl.py"
        impl.write_bytes(b"# evaluator impl (test-scoped)\n")
        env["SWARM_F2_EVALUATOR_IMPL"] = str(impl)
    return env


def _clean_attestation():
    """A verified clean-room attestation: every dimension observed and satisfied.

    The readiness gate refuses a caller-ASSERTED string receipt, so a READY
    fixture must carry real observed evidence.
    """
    from qwen_train import f2_isolation as iso

    denied = {
        ("tcp", "example.com", 80): (iso.OUTCOME_DENIED, "refused", ""),
        ("tcp", "api.github.com", 443): (iso.OUTCOME_DENIED, "refused", ""),
        ("tcp", "93.184.216.34", 80): (iso.OUTCOME_DENIED, "winerror=10013", ""),
        ("dns", "8.8.8.8", 53): (iso.OUTCOME_DENIED, "timed out", ""),
        ("tcp", "2606:4700:4700::1111", 443): (iso.OUTCOME_DENIED, "refused", ""),
        ("tcp", "127.0.0.1", 1): (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1"),
        ("tcp", "127.0.0.1", 8000): (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1"),
    }

    def probe(protocol, host, port):
        return denied.get((protocol, host, port), (iso.OUTCOME_UNKNOWN, "not probed", ""))

    return iso.run_egress_probes(
        probe=probe,
        loopback_targets=(("127.0.0.1", 1),),
        required_services=(("backend", "127.0.0.1", 8000),),
        dns_probe=lambda: (iso.OUTCOME_DENIED, "resolver refused"),
        ipv6_available=lambda: True,
        interface_inventory=("Loopback Pseudo-Interface 1", "Wi-Fi"),
        policy_identity="windows-defender-firewall",
        policy_sha256="ab" * 32,
        policy_assertions={"proxy": "denied", "alternate_interface": "denied"},
        arm_id="arm-T-001",
        rollout_id="rollout-77",
        workspace="C:/isolated/task-001",
        executing_user="DOMAIN\\f2arm",
        interpreter_path="C:/Python314/python.exe",
        interpreter_sha256="cd" * 32,
        spawn_image_inventory=("C:/Python314/python.exe",),
        enforcement_scope="windows_account",
    ).to_dict()


def _verified_evidence(
    state: str, result: str, store: Path | None = None,
    instance_id: str = "inst-1", base_commit: str = "c0ffee",
):
    """A real, VERIFIED execution evidence record built by f2_evidence itself.

    Readiness validates base/gold with the same validator the independent
    verifier uses, against the REAL trusted store -- so the retained artifacts
    must actually exist. Writing them here is what makes the fixture genuine.
    """
    from qwen_train.f2_evidence import build_evidence_record
    from qwen_train.f2_governance import (
        ROLE_RUN_LOG,
        ROLE_TEST_OUTPUT,
        ArtifactRef,
        build_execution_identity,
    )

    out_body = f"{state} retained test output\n".encode()
    log_body = f"{state} retained run log\n".encode()
    ref = ArtifactRef(
        name=f"{state}-test.out", digest=hashlib.sha256(out_body).hexdigest(),
        size_bytes=len(out_body), role=ROLE_TEST_OUTPUT,
    )
    log = ArtifactRef(
        name=f"{state}-run.log", digest=hashlib.sha256(log_body).hexdigest(),
        size_bytes=len(log_body), role=ROLE_RUN_LOG,
    )
    if store is not None:
        store.mkdir(parents=True, exist_ok=True)
        (store / ref.name).write_bytes(out_body)
        (store / log.name).write_bytes(log_body)

    ident = build_execution_identity(
        instance_id=instance_id, repository="o/r", base_commit=base_commit,
        execution_state_identity=state,
        execution_state_digest="d" * 64,
        evaluator=_test_authorization(),
        environment_identity="test-env", test_command="pytest -q",
        test_output=ref, run_log=log,
    )
    rec = build_evidence_record(
        instance_id=instance_id, repository="o/r", base_commit=base_commit,
        execution_state_identity=state, execution_state_digest=ident.digest(),
        test_command="pytest -q", environment_identity="test-env",
        evaluator_identity=_test_authorization().evaluator_id,
        evaluator_version=_test_authorization().version,
        execution_started_at="2026-10-04T00:00:00Z",
        execution_finished_at="2026-10-04T00:01:00Z",
        execution_result=result, failure_class="",
        test_output_artifact=ref, run_log_artifact=log,
    )
    return rec.to_dict()


def _test_authorization():
    from qwen_train.f2_evaluator import identity_fields
    from qwen_train.f2_governance import EvaluatorAuthorization

    f = identity_fields()
    return EvaluatorAuthorization(
        evaluator_id=f["evaluator_id"], version=f["version"],
        implementation_digest=f["implementation_digest"],
        procedure_id=f["procedure_id"], protocol_version=f["protocol_version"],
    )


def _clean_mutation():
    return {
        "captured": True, "repository": "C:/task/repo",
        "touched_paths": ["src/pkg/mod.py"], "touched_count": 1,
        "base_commit_expected": "c0ffee", "head_after": "c0ffee",
        "head_unchanged": True, "refs_after": [],
        "ignored_paths_sample": [], "ignored_count": 0,
    }


_POP_STORE = pathlib.Path(tempfile.mkdtemp(prefix="f2pop_store_"))
atexit.register(shutil.rmtree, _POP_STORE, ignore_errors=True)
_ADMITTED_CACHE: dict[int, object] = {}


def _authorized_evaluators():
    f = _test_authorization()
    return {f.evaluator_id: (f.version,)}


def _admitted_manifest(
    n: int, *, cutoff: str = "2024-01-01", created_at: str = "2025-06-01",
    store: Path | None = None,
):
    """A GENUINE population manifest with ``n`` admitted tasks.

    Each admitted entry requires verified S8 base/gold evidence, so this builds
    real evidence records against a real store -- it cannot be fabricated by
    declaring a number.
    """
    from qwen_train.f2_population import contamination_policy_record, screen_pool_rows

    store = _POP_STORE if store is None else store
    base = "c0ffee" * 6 + "beef"  # 40-hex, S2 requires len >= 7
    rows, recs, sets = [], {}, {}
    for i in range(n):
        iid = f"o__r-{i}"
        rows.append({
            "instance_id": iid, "repo": "o/r", "base_commit": base,
            "test_cmd": "pytest -q",
            "fail_to_pass": ["tests/t.py::a"], "pass_to_pass": ["tests/t.py::b"],
            "created_at": created_at, "usable": True,
        })
        recs[iid] = (
            _verified_evidence("base", "fail", store, instance_id=iid, base_commit=base),
            _verified_evidence("gold", "pass", store, instance_id=iid, base_commit=base),
        )
        sets[iid] = ["pkg/mod.py"]
    manifest = screen_pool_rows(
        rows, relevant_file_sets=sets, evidence_records=recs,
        artifact_root=store, authorized_evaluators=_authorized_evaluators(),
        model_cutoff=cutoff,
        contamination_policy=contamination_policy_record(cutoff),
    )
    manifest.verify()
    return manifest


def _population_manifest(n: int = FROZEN_MIN_PAIRS):
    if n not in _ADMITTED_CACHE:
        _ADMITTED_CACHE[n] = _admitted_manifest(n)
    return _ADMITTED_CACHE[n]


def _all_supplied(store: Path | None = None):
    return {
        "population": _population_manifest(),
        "base_artifacts": _verified_evidence("base", "fail", store),
        "base_evidence": _verified_evidence("base", "fail", store),
        "gold_artifacts": _verified_evidence("gold", "pass", store),
        "gold_evidence": _verified_evidence("gold", "pass", store),
        "t_manifest": _t_manifest(),
        "x_manifest": _x_manifest(),
        "c0_manifest": _t_manifest(),  # any verify_manifest-valid artifact exercises the check
        "clean_room_mutation": _clean_mutation(),
        "declared_test_files": ["tests/test_mod.py"],
        "relevant_file_set": ["src/pkg/mod.py"],
        "no_egress_attestation": _clean_attestation(),
        "authorizations": {"q10": True, "q12": True, "q13": True},
    }


@pytest.fixture()
def ready(tmp_path):
    """A READY environment + supplied set backed by a REAL trusted store."""
    store = tmp_path / "store"
    supplied = _all_supplied(store)
    env = _all_env(tmp_path)
    return env, supplied


class TestFailClosed:
    def test_empty_environment_is_not_ready(self):
        rep = evaluate_f2_readiness(env={}, supplied={})
        assert rep.ready is False
        # Two items are now pure capability-presence checks. The third former
        # capability item, delivery_instrumentation, additionally requires the
        # emission gate and the evaluator report destination to be configured,
        # so it fails closed on an empty environment like every other
        # evidence-producing item.
        assert len(rep.blockers) == len(READINESS_ITEMS) - 2
        for c in rep.checks:
            if not c.satisfied:
                assert c.failure_behavior  # every blocker states its failure behavior

    def test_code_presence_is_satisfied_locally(self):
        rep = evaluate_f2_readiness(env={}, supplied={})
        by = {c.item: c for c in rep.checks}
        assert by["regrade_bundle_verification"].satisfied
        assert by["statistical_analysis"].satisfied
        # Capability exists but is not ENABLED, so it must not report satisfied.
        assert not by["delivery_instrumentation"].satisfied
        assert "SWARM_F2_EMIT_BUNDLE" in by["delivery_instrumentation"].operator_action

    def test_operator_only_items_are_classified(self):
        rep = evaluate_f2_readiness(env={}, supplied={})
        by = {c.item: c for c in rep.checks}
        assert by["receipt_key"].status == "OPERATOR ACTION REQUIRED"
        assert by["q9_no_egress"].status == "OPERATOR ACTION REQUIRED"


class TestValidation:
    def test_bad_weights_digest_rejected(self):
        env = _all_env()
        env["SWARM_DISTILLER_WEIGHTS_DIGEST"] = "not-a-digest"
        by = {c.item: c for c in evaluate_f2_readiness(env=env, supplied={}).checks}
        assert by["weights_digest"].satisfied is False

    def test_relative_store_root_rejected(self):
        env = _all_env()
        env["SWARM_F2_ARTIFACT_ROOT"] = "relative/store"
        by = {c.item: c for c in evaluate_f2_readiness(env=env, supplied={}).checks}
        assert by["trusted_artifact_store"].satisfied is False

    def test_missing_evaluator_impl_rejected(self):
        env = _all_env()  # no impl path
        by = {c.item: c for c in evaluate_f2_readiness(env=env, supplied={}).checks}
        assert by["evaluator_implementation_digest"].satisfied is False

    def test_population_shortfall_blocks(self):
        s = _all_supplied()
        s["population"] = _population_manifest(1)  # a GENUINE but undersized manifest
        by = {c.item: c for c in evaluate_f2_readiness(env=_all_env(), supplied=s).checks}
        assert by["protected_population"].satisfied is False

    def test_no_egress_config_is_not_evidence(self):
        s = _all_supplied()
        del s["no_egress_attestation"]
        by = {c.item: c for c in evaluate_f2_readiness(env=_all_env(), supplied=s).checks}
        assert by["q9_no_egress"].satisfied is False
        assert "PRIVILEGED HOST ACTION" in by["q9_no_egress"].operator_action

    def test_partial_no_egress_probe_rejected(self):
        """An incomplete observation must not satisfy the gate."""
        s = _all_supplied()
        from qwen_train import f2_isolation as iso

        att = iso.parse_attestation(s["no_egress_attestation"])
        broken = list(att.probes)
        for i, p in enumerate(broken):
            if p.dimension == "udp":
                broken[i] = iso.ProbeResult(
                    dimension=p.dimension, target=p.target, protocol=p.protocol,
                    outcome=iso.OUTCOME_UNKNOWN, detail="unprobed", pid=p.pid, at=p.at,
                )
        s["no_egress_attestation"] = iso.IsolationAttestation(
            schema=att.schema, arm_id=att.arm_id, rollout_id=att.rollout_id,
            workspace=att.workspace, observer_pid=att.observer_pid,
            policy_identity=att.policy_identity, policy_sha256=att.policy_sha256,
            interface_inventory=att.interface_inventory, dns_behavior=att.dns_behavior,
            negative_control=att.negative_control, probes=tuple(broken),
            required_services=att.required_services,
            policy_assertions=att.policy_assertions,
        ).to_dict()
        by = {c.item: c for c in evaluate_f2_readiness(env=_all_env(), supplied=s).checks}
        assert by["q9_no_egress"].satisfied is False

    def test_missing_authorization_blocks(self):
        s = _all_supplied()
        s["authorizations"] = {"q10": True, "q12": False, "q13": True}
        rep = evaluate_f2_readiness(env=_all_env(), supplied=s)
        by = {c.item: c for c in rep.checks}
        assert by["q10_q12_q13_authorization"].satisfied is False
        assert rep.ready is False


class TestFullySupplied:
    def test_all_prerequisites_supplied_is_ready(self, tmp_path):
        rep = evaluate_f2_readiness(env=_all_env(tmp_path), supplied=_all_supplied(tmp_path / "store"))
        assert rep.ready is True, rep.blockers
        assert rep.blockers == ()

    def test_report_never_contains_the_receipt_secret(self, tmp_path):
        rep = evaluate_f2_readiness(env=_all_env(tmp_path), supplied=_all_supplied(tmp_path / "store"))
        blob = str(rep.to_dict())
        assert "test-scoped-receipt-key" not in blob


class TestStrengthenedPrerequisites:
    """F-1: prerequisites that deterministically fail the execution path AFTER
    READY=True are now covered by existing items rather than a new item, so the
    authorized 18-item contract is unchanged."""

    def _item(self, env, item):
        rep = evaluate_f2_readiness(env=env, supplied=_all_supplied())
        return {c.item: c for c in rep.checks}[item]

    def test_evaluator_procedure_is_now_required(self, tmp_path):
        env = _all_env(tmp_path)
        env.pop("SWARM_F2_EVALUATOR_PROCEDURE")
        c = self._item(env, "evaluator_identity")
        assert not c.satisfied
        assert "SWARM_F2_EVALUATOR_PROCEDURE" in c.detail
        assert c.failure_behavior

    def test_evaluator_identity_satisfied_with_all_three(self, tmp_path):
        c = self._item(_all_env(tmp_path), "evaluator_identity")
        assert c.satisfied
        assert "procedure" in c.detail

    def test_emit_bundle_disabled_blocks_readiness(self, tmp_path):
        env = _all_env(tmp_path)
        env["SWARM_F2_EMIT_BUNDLE"] = "0"
        c = self._item(env, "delivery_instrumentation")
        assert not c.satisfied
        assert "SWARM_F2_EMIT_BUNDLE" in c.detail

    @pytest.mark.parametrize("val", ["", "  ", "false", "no", "off", "maybe"])
    def test_emit_bundle_truthiness_is_strict(self, tmp_path, val):
        env = _all_env(tmp_path)
        env["SWARM_F2_EMIT_BUNDLE"] = val
        assert not self._item(env, "delivery_instrumentation").satisfied

    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
    def test_emit_bundle_accepts_documented_truthy(self, tmp_path, val):
        env = _all_env(tmp_path)
        env["SWARM_F2_EMIT_BUNDLE"] = val
        assert self._item(env, "delivery_instrumentation").satisfied

    def test_missing_outcome_report_path_blocks_readiness(self, tmp_path):
        env = _all_env(tmp_path)
        env.pop("SWARM_F2_TASK_OUTCOME_REPORT")
        c = self._item(env, "delivery_instrumentation")
        assert not c.satisfied
        assert "SWARM_F2_TASK_OUTCOME_REPORT" in c.detail

    def test_outcome_report_only_need_be_configured_not_present(self, tmp_path):
        """The report is a per-arm RUN product; demanding the file now would be
        a category error. Only its destination must be designated."""
        env = _all_env(tmp_path)
        env["SWARM_F2_TASK_OUTCOME_REPORT"] = str(tmp_path / "not_created_yet.json")
        assert self._item(env, "delivery_instrumentation").satisfied

    def test_ready_becomes_false_when_emit_bundle_is_off(self, tmp_path):
        env = _all_env(tmp_path)
        env["SWARM_F2_EMIT_BUNDLE"] = "0"
        rep = evaluate_f2_readiness(env=env, supplied=_all_supplied())
        assert rep.ready is False
        assert "delivery_instrumentation" in rep.blockers

    def test_ready_becomes_false_when_procedure_is_absent(self, tmp_path):
        env = _all_env(tmp_path)
        env.pop("SWARM_F2_EVALUATOR_PROCEDURE")
        rep = evaluate_f2_readiness(env=env, supplied=_all_supplied())
        assert rep.ready is False
        assert "evaluator_identity" in rep.blockers

    def test_item_count_unchanged(self):
        """The authorized contract is 18 items; strengthening must not add any."""
        assert len(READINESS_ITEMS) == 18

    def test_readiness_never_echoes_the_receipt_key(self, tmp_path):
        rep = evaluate_f2_readiness(env=_all_env(tmp_path), supplied=_all_supplied(tmp_path / "store"))
        assert rep.ready is True
        blob = repr(rep)
        assert "test-scoped-receipt-key" not in blob

    def test_receipt_value_is_never_read(self, tmp_path, monkeypatch):
        """The gate tests PRESENCE only. With an explicit env mapping it does not
        consult os.environ at all, so the process environment cannot leak into the
        report."""
        import os as _os

        seen = []
        real_get = _os.environ.get

        def spy(key, default=None):
            seen.append(key)
            return real_get(key, default)

        monkeypatch.setattr(_os.environ, "get", spy, raising=False)

        rep = evaluate_f2_readiness(env=_all_env(tmp_path), supplied=_all_supplied(tmp_path / "store"))
        assert rep.ready is True
        assert seen == [], f"explicit env must not fall through to os.environ: {seen}"

        # And with no env mapping the key is read for presence only; its value
        # never reaches the report.
        seen.clear()
        monkeypatch.setenv("SWARM_RECEIPT_KEY", "sentinel-secret-value")
        rep2 = evaluate_f2_readiness(supplied=_all_supplied())
        assert "SWARM_RECEIPT_KEY" in seen
        assert "sentinel-secret-value" not in repr(rep2)
        by = {c.item: c for c in rep2.checks}
        assert by["receipt_key"].satisfied
        assert "sentinel" not in by["receipt_key"].detail


class TestIsolationAttestationIntegration:
    """The readiness gate must be able to VERIFY an attestation rather than
    trust a caller's seven strings."""

    def _att(self, **over):
        from qwen_train import f2_isolation as iso

        denied = {
            ("tcp", "example.com", 80): (iso.OUTCOME_DENIED, "refused", ""),
            ("tcp", "api.github.com", 443): (iso.OUTCOME_DENIED, "refused", ""),
            ("tcp", "93.184.216.34", 80): (iso.OUTCOME_DENIED, "winerror=10013", ""),
            ("dns", "8.8.8.8", 53): (iso.OUTCOME_DENIED, "timed out", ""),
            ("tcp", "2606:4700:4700::1111", 443): (iso.OUTCOME_DENIED, "refused", ""),
            ("tcp", "127.0.0.1", 1): (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1"),
            ("tcp", "127.0.0.1", 8000): (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1"),
        }

        def probe(protocol, host, port):
            return denied.get((protocol, host, port), (iso.OUTCOME_UNKNOWN, "np", ""))

        kwargs = dict(
            probe=probe,
            loopback_targets=(("127.0.0.1", 1),),
            required_services=(("backend", "127.0.0.1", 8000),),
            dns_probe=lambda: (iso.OUTCOME_DENIED, "resolver refused"),
            ipv6_available=lambda: True,
            interface_inventory=("Loopback Pseudo-Interface 1", "Wi-Fi"),
            policy_identity="windows-defender-firewall",
            policy_sha256="ab" * 32,
            policy_assertions={"proxy": "denied", "alternate_interface": "denied"},
            arm_id="arm-T-001",
            rollout_id="rollout-77",
            workspace="C:/isolated/task-001",
            executing_user="DOMAIN\\f2arm",
            interpreter_path="C:/Python314/python.exe",
            interpreter_sha256="cd" * 32,
            spawn_image_inventory=("C:/Python314/python.exe",),
            enforcement_scope="windows_account",
        )
        kwargs.update(over)
        return iso.run_egress_probes(**kwargs)

    def test_verified_attestation_satisfies_the_gate(self):
        from qwen_train.f2_readiness import _check_no_egress

        ok, detail, _ = _check_no_egress({"no_egress_attestation": self._att().to_dict()})
        assert ok, detail
        assert "digest" in detail

    def test_unenforced_attestation_fails_the_gate(self):
        from qwen_train.f2_readiness import _check_no_egress

        from qwen_train import f2_isolation as iso
        denied = {("tcp", "api.github.com", 443): (iso.OUTCOME_PERMITTED, "connected", "10.0.0.5")}
        att = self._att(probe=lambda p, h, pt: denied.get(
            (p, h, pt), (iso.OUTCOME_DENIED, "refused", "")))
        ok, detail, _ = _check_no_egress({"no_egress_attestation": att.to_dict()})
        assert not ok
        assert "https" in detail

    def test_attestation_overrides_a_lying_string_receipt(self):
        """A caller cannot claim 'denied' in strings while the attestation says
        egress was reachable."""
        from qwen_train.f2_readiness import _check_no_egress
        from qwen_train import f2_isolation as iso

        att = self._att(probe=lambda p, h, pt: (
            (iso.OUTCOME_PERMITTED, "connected", "10.0.0.5")
            if (p, h, pt) == ("tcp", "api.github.com", 443)
            else (iso.OUTCOME_DENIED, "refused", "")
        ))
        supplied = {
            "no_egress_attestation": att.to_dict(),
            "no_egress": {"http": "denied", "https": "denied", "tcp": "denied",
                          "udp": "denied", "ipv6": "denied", "proxy": "denied",
                          "loopback": "ok"},
        }
        ok, detail, _ = _check_no_egress(supplied)
        assert not ok, "the attestation must win over the caller's strings"

    def test_malformed_attestation_fails_closed(self):
        from qwen_train.f2_readiness import _check_no_egress

        ok, detail, _ = _check_no_egress({"no_egress_attestation": {"schema": "wrong"}})
        assert not ok
        assert "malformed" in detail

    def test_missing_required_service_coverage_fails(self):
        from qwen_train.f2_readiness import _check_no_egress

        ok, detail, _ = _check_no_egress({
            "no_egress_attestation": self._att().to_dict(),
            "required_local_services": ["qdrant 127.0.0.1:6333"],
        })
        assert not ok
        assert "required_service_coverage" in detail or "UNPROVEN" in detail

    def test_string_receipt_is_REFUSED_not_accepted(self):
        """A typed word must never masquerade as measured isolation."""
        from qwen_train.f2_readiness import _check_no_egress

        ok, detail, action = _check_no_egress({"no_egress": {
            "http": "denied", "https": "denied", "tcp": "denied", "udp": "denied",
            "ipv6": "denied", "proxy": "denied", "loopback": "ok"}})
        assert not ok, "an asserted string receipt must not satisfy the gate"
        assert "caller-ASSERTED" in detail
        assert "PRIVILEGED HOST ACTION" in action

    def test_full_readiness_accepts_a_verified_attestation(self, tmp_path):
        att = self._att().to_dict()
        supplied = _all_supplied()
        supplied["no_egress_attestation"] = att
        rep = evaluate_f2_readiness(env=_all_env(tmp_path), supplied=_all_supplied(tmp_path / "store"))
        assert rep.ready is True, rep.blockers
        by = {c.item: c for c in rep.checks}
        assert "digest" in by["q9_no_egress"].detail


class TestEvidenceIsVerifiedNotAsserted:
    """Base/gold and clean-room must be VERIFIED, never taken on trust."""

    def test_asserted_base_evidence_is_refused(self):
        from qwen_train.f2_readiness import _check_artifacts

        ok, detail, action = _check_artifacts(
            {"base_artifacts": {"verified": True}}, _all_env(), "base_artifacts", "base"
        )
        assert not ok, "a caller-asserted boolean must not satisfy base evidence"
        assert "caller-ASSERTED" in detail
        assert "EvidenceRecord" in action

    def test_asserted_gold_evidence_is_refused(self):
        from qwen_train.f2_readiness import _check_artifacts

        ok, detail, _ = _check_artifacts(
            {"gold_artifacts": {"verified": True}}, _all_env(), "gold_artifacts", "gold"
        )
        assert not ok
        assert "caller-ASSERTED" in detail

    def test_base_declaring_pass_is_refused(self):
        """base=FAIL and gold=PASS are the frozen contract."""
        from qwen_train.f2_readiness import _check_artifacts

        tmp = tmp_path_for(self)
        rec = _verified_evidence("base", "pass", tmp / "store")
        ok, detail, _ = _check_artifacts(
            {"base_artifacts": rec}, _all_env(tmp), "base_artifacts", "base"
        )
        assert not ok
        assert "requires base=FAIL" in detail

    def test_gold_declaring_fail_is_refused(self):
        from qwen_train.f2_readiness import _check_artifacts

        tmp = tmp_path_for(self)
        rec = _verified_evidence("gold", "fail", tmp / "store")
        ok, detail, _ = _check_artifacts(
            {"gold_artifacts": rec}, _all_env(tmp), "gold_artifacts", "gold"
        )
        assert not ok
        assert "gold=PASS" in detail

    def test_evidence_missing_from_the_store_is_refused(self):
        """A record whose retained artifacts are absent cannot establish provenance."""
        from qwen_train.f2_readiness import _check_artifacts

        tmp = tmp_path_for(self)
        rec = _verified_evidence("base", "fail", None)  # nothing written
        ok, detail, _ = _check_artifacts(
            {"base_artifacts": rec}, _all_env(tmp), "base_artifacts", "base"
        )
        assert not ok
        assert "not VERIFIED" in detail

    def test_unauthorized_evaluator_version_is_refused(self):
        """Evidence whose declared evaluator version is not authorized is refused.

        The evidence record carries evaluator identity and version but NOT the
        procedure id, so this gate can bind identity+version and must not claim
        more. Asserting a procedure check here would be false assurance.
        """
        from qwen_train.f2_readiness import _check_artifacts

        tmp = tmp_path_for(self)
        rec = _verified_evidence("base", "fail", tmp / "store")
        env = _all_env(tmp)
        env["SWARM_F2_EVALUATOR_VERSION"] = "9.9.9-not-authorized"
        ok, detail, _ = _check_artifacts(
            {"base_artifacts": rec}, env, "base_artifacts", "base"
        )
        assert not ok
        assert "UNAUTHORIZED_PROCEDURE" in detail

    def test_unrecognized_evaluator_identity_is_refused(self):
        from qwen_train.f2_readiness import _check_artifacts

        tmp = tmp_path_for(self)
        rec = _verified_evidence("base", "fail", tmp / "store")
        env = _all_env(tmp)
        env["SWARM_F2_EVALUATOR_ID"] = "some_other_evaluator"
        ok, detail, _ = _check_artifacts(
            {"base_artifacts": rec}, env, "base_artifacts", "base"
        )
        assert not ok
        assert "not an authorized procedure" in detail

    def test_missing_artifact_root_is_refused(self):
        from qwen_train.f2_readiness import _check_artifacts

        tmp = tmp_path_for(self)
        rec = _verified_evidence("base", "fail", tmp)
        env = _all_env(tmp)
        env.pop("SWARM_F2_ARTIFACT_ROOT", None)
        ok, detail, _ = _check_artifacts(
            {"base_artifacts": rec}, env, "base_artifacts", "base"
        )
        assert not ok
        assert "artifact root" in detail

    def test_real_evidence_satisfies_the_item(self, tmp_path):
        from qwen_train.f2_readiness import _check_artifacts

        store = tmp_path / "store"
        rec = _verified_evidence("base", "fail", store)
        ok, detail, _ = _check_artifacts(
            {"base_artifacts": rec}, _all_env(tmp_path), "base_artifacts", "base"
        )
        assert ok, detail
        assert "trusted store" in detail


class TestCleanRoomIsVerifiedNotAsserted:
    def test_asserted_isolated_boolean_is_refused(self):
        from qwen_train.f2_readiness import _check_clean_room

        ok, detail, action = _check_clean_room({"clean_room": {"isolated": True}})
        assert not ok, "a caller-asserted boolean must not satisfy clean-room"
        assert "caller-ASSERTED" in detail
        assert "clean_room_mutation" in action

    def test_no_evidence_is_refused(self):
        from qwen_train.f2_readiness import _check_clean_room

        ok, detail, _ = _check_clean_room({})
        assert not ok
        assert "no clean-room evidence" in detail

    def test_verified_clean_mutation_satisfies_the_item(self):
        from qwen_train.f2_readiness import _check_clean_room

        ok, detail, _ = _check_clean_room({
            "clean_room_mutation": _clean_mutation(),
            "declared_test_files": ["tests/test_mod.py"],
            "relevant_file_set": ["src/pkg/mod.py"],
        })
        assert ok, detail
        assert "observed workspace evidence" in detail

    def test_test_tampering_refuses_clean_room_despite_any_claim(self):
        from qwen_train.f2_readiness import _check_clean_room

        tampered = dict(_clean_mutation())
        tampered["touched_paths"] = ["tests/test_mod.py"]
        tampered["touched_count"] = 1
        ok, detail, _ = _check_clean_room({
            "clean_room_mutation": tampered,
            "declared_test_files": ["tests/test_mod.py"],
            "clean_room": {"isolated": True},
        })
        assert not ok
        assert "REFUSED" in detail

    def test_unobservable_mutation_refuses_clean_room(self):
        from qwen_train.f2_readiness import _check_clean_room

        ok, detail, _ = _check_clean_room({
            "clean_room_mutation": {"captured": False, "error": "git missing"},
        })
        assert not ok
        assert "REFUSED" in detail


class TestPopulationCountedQuantity:
    """The population prerequisite must be backed by a VERIFIED manifest artifact.

    F2-CLARIFICATION-007: this gate counts ADMITTED tasks (AUTH-028), while the
    frozen design's ``n = 300`` counts ANALYZABLE complete pairs -- a different
    quantity. F2-IMPL-AUTH-030: the count is now DERIVED from a verified
    ``PopulationManifest``; a caller-supplied number or a mapping that merely
    declares one is not evidence and is refused.
    """

    def test_the_minimum_is_still_exactly_300(self):
        from qwen_train.f2_readiness import FROZEN_MIN_PAIRS

        assert FROZEN_MIN_PAIRS == 300

    # -- Case A: a bare count is not evidence ------------------------------

    def test_bare_admitted_count_fails(self):
        from qwen_train.f2_readiness import FROZEN_MIN_PAIRS, _check_population

        ok, detail, _ = _check_population({"population": {"admitted": FROZEN_MIN_PAIRS}})
        assert ok is False
        assert "not a verifiable PopulationManifest" in detail

    # -- Case B: count + declared cutoff, still no population evidence -----

    def test_count_plus_declared_cutoff_without_a_manifest_fails(self):
        from qwen_train.f2_readiness import FROZEN_MIN_PAIRS, _check_population

        ok, detail, _ = _check_population(
            {
                "population": {
                    "admitted": FROZEN_MIN_PAIRS,
                    "contamination_policy": {"declared": True, "cutoff": "2024-01-01"},
                }
            }
        )
        assert ok is False
        assert "not a verifiable PopulationManifest" in detail

    # -- Case C: UNKNOWN contamination cannot be reported clean ------------

    def test_declared_policy_with_unknown_contamination_fails(self):
        from qwen_train.f2_readiness import FROZEN_MIN_PAIRS, _check_population

        ok, _, _ = _check_population(
            {
                "population": {
                    "admitted": FROZEN_MIN_PAIRS,
                    "contamination_classes": {"UNKNOWN": FROZEN_MIN_PAIRS},
                    "contamination_policy": {"declared": True, "cutoff": "2024-01-01"},
                }
            }
        )
        assert ok is False

    def test_unknown_contamination_manifest_derives_zero_admitted(self):
        """A real manifest with NO cutoff has 0 CLEAN entries -> 0 admitted."""
        from qwen_train.f2_readiness import _check_population

        man = _admitted_manifest(3, cutoff="")          # declared cutoff empty
        assert len(man.admitted) == 0
        assert all(e.contamination_class == "UNKNOWN" for e in man.entries)
        ok, _, _ = _check_population({"population": man})
        assert ok is False

    # -- Case D: caller CLEAN count conflicting with the artifact ----------

    def test_caller_clean_claim_conflicting_with_manifest_is_refused(self):
        from qwen_train.f2_readiness import FROZEN_MIN_PAIRS, _check_population

        # A hand-written "census" claiming 300 CLEAN admitted tasks, with no
        # manifest behind it, is refused outright (it cannot be integrity-checked).
        ok, detail, _ = _check_population(
            {
                "population": {
                    "admitted": FROZEN_MIN_PAIRS,
                    "contamination_classes": {"CLEAN": FROZEN_MIN_PAIRS},
                    "admitted_clean": FROZEN_MIN_PAIRS,
                    "contamination_policy": {"declared": True, "cutoff": "2024-01-01"},
                }
            }
        )
        assert ok is False
        assert "declared count is not evidence" in detail

    # -- Case E: a genuine but undersized manifest fails -------------------

    def test_genuine_manifest_below_minimum_fails(self):
        from qwen_train.f2_readiness import _check_population

        man = _admitted_manifest(2)
        assert len(man.admitted) == 2
        ok, detail, action = _check_population({"population": man})
        assert ok is False
        assert "admitted=2" in detail and "oversampled" in action

    # -- Case F: a genuine manifest meeting the contract passes ------------

    def test_verified_manifest_meeting_contract_passes(self):
        from qwen_train.f2_readiness import FROZEN_MIN_PAIRS, _check_population

        man = _population_manifest()
        assert len(man.admitted) >= FROZEN_MIN_PAIRS
        ok, detail, _ = _check_population({"population": man})
        assert ok is True, detail
        assert "verified manifest" in detail

    def test_derived_count_is_not_taken_from_the_caller(self):
        """The same manifest passes even though no number is supplied anywhere."""
        from qwen_train.f2_readiness import _check_population

        man = _population_manifest()
        assert not hasattr(man, "admitted_count")
        ok, _, _ = _check_population({"population": man})
        assert ok is True

    def test_full_readiness_population_item_uses_the_manifest(self):
        """The production path (evaluate_f2_readiness) derives the item from the artifact."""
        man = _population_manifest(1)
        s = _all_supplied()
        s["population"] = man
        by = {c.item: c for c in evaluate_f2_readiness(env=_all_env(), supplied=s).checks}
        item = by["protected_population"]
        assert item.satisfied is False
        assert "admitted=1" in item.detail

    def test_duplicate_identity_manifest_fails_verification(self):
        """A manifest carrying a duplicate identity fails closed (nothing derived)."""
        from dataclasses import replace

        from qwen_train.f2_population import PopulationManifest
        from qwen_train.f2_readiness import _check_population

        base = _population_manifest(1)
        dup = replace(base.entries[0])
        try:
            bad = PopulationManifest(entries=(base.entries[0], dup))
        except Exception:  # pragma: no cover - construction is proof-token gated
            return
        ok, detail, _ = _check_population({"population": bad})
        assert ok is False
        assert "verification" in detail
