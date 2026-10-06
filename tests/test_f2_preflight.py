"""Tests for the F2 execution preflight and the isolation probe CLI.

The properties that matter are that the preflight NEVER collapses categories,
NEVER reports an external gate as satisfied, NEVER reads a secret value, and
produces a stable machine-readable result.
"""
from __future__ import annotations

import json

import pytest

from qwen_train import f2_preflight as pf
from qwen_train import f2_evaluator as fe
from qwen_train import f2_isolation as iso


@pytest.fixture()
def clean_env(monkeypatch):
    """Every operator-supplied prerequisite present EXCEPT the receipt key."""
    for var in (
        "SWARM_DISTILLER_MODEL", "SWARM_DISTILLER_WEIGHTS_DIGEST",
        "SWARM_F2_EVALUATOR_ID", "SWARM_F2_EVALUATOR_VERSION",
        "SWARM_F2_EVALUATOR_PROCEDURE", "SWARM_F2_ARTIFACT_ROOT",
        "SWARM_F2_ARTIFACT_RETENTION_DAYS", "SWARM_F2_EMIT_BUNDLE",
        "SWARM_F2_TASK_OUTCOME_REPORT", "SWARM_RECEIPT_KEY",
        "SWARM_F2_EVALUATOR_IMPL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SWARM_DISTILLER_MODEL", "robs4b")
    monkeypatch.setenv("SWARM_DISTILLER_WEIGHTS_DIGEST", "a" * 64)
    monkeypatch.setenv("SWARM_F2_EVALUATOR_ID", fe.EVALUATOR_ID)
    monkeypatch.setenv("SWARM_F2_EVALUATOR_VERSION", fe.EVALUATOR_VERSION)
    monkeypatch.setenv("SWARM_F2_EVALUATOR_PROCEDURE", fe.PROCEDURE_ID)
    monkeypatch.setenv("SWARM_F2_ARTIFACT_ROOT", "C:/trusted/f2")
    monkeypatch.setenv("SWARM_F2_ARTIFACT_RETENTION_DAYS", "365")
    monkeypatch.setenv("SWARM_F2_EMIT_BUNDLE", "1")
    monkeypatch.setenv("SWARM_F2_TASK_OUTCOME_REPORT", "C:/trusted/f2/outcome.json")
    return monkeypatch


class TestCategoriesAreNeverCollapsed:
    def test_every_category_is_used_or_empty_but_known(self):
        rep = pf.run_preflight()
        assert set(rep["counts"]) == set(pf.CATEGORIES)

    def test_no_finding_has_an_unknown_category(self):
        rep = pf.run_preflight()
        for f in rep["findings"]:
            assert f["category"] in pf.CATEGORIES

    def test_external_gates_are_never_reported_satisfied(self):
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        # These cannot be satisfied by any repository change.
        assert by["evidence.s8_base_gold"]["status"] != "present"
        assert by["evidence.active_lesson"]["status"] != "present"
        assert by["host.egress_enforcement"]["status"] == "not_enforced"
        assert by["authz.population_acquisition"]["status"] == "not_authorized"

    def test_implemented_findings_are_all_present(self):
        rep = pf.run_preflight()
        impl = [f for f in rep["findings"] if f["category"] == pf.IMPLEMENTED]
        assert impl, "repository capability must be reported"
        # Code capabilities report "present"; verified provenance links report the
        # stronger, more specific "PROVEN". Neither may be anything else.
        assert all(f["status"] in ("present", "PROVEN") for f in impl), [
            f["id"] for f in impl if f["status"] not in ("present", "PROVEN")
        ]

    def test_not_executed_is_a_separate_category(self):
        rep = pf.run_preflight()
        ne = [f for f in rep["findings"] if f["category"] == pf.NOT_EXECUTED]
        assert ne
        assert all(f["status"] == "not_executed" for f in ne)


class TestSecretSafety:
    def test_receipt_key_value_never_appears(self, clean_env):
        clean_env.setenv("SWARM_RECEIPT_KEY", "sentinel-secret-value")
        rep = pf.run_preflight()
        blob = json.dumps(rep)
        assert "sentinel-secret-value" not in blob
        by = {f["id"]: f for f in rep["findings"]}
        assert by["env.receipt_key"]["status"] == "present"
        assert "value never read" in by["env.receipt_key"]["detail"]

    def test_absent_receipt_key_is_operator_action_not_failure(self, clean_env):
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        assert by["env.receipt_key"]["category"] == pf.OPERATOR_ACTION
        assert by["env.receipt_key"]["status"] == "absent"


class TestResultSemantics:
    def test_blocked_while_external_gates_remain(self, clean_env):
        rep = pf.run_preflight()
        assert rep["result"] == "BLOCKED"
        assert rep["blocking_count"] > 0

    def test_every_blocking_finding_has_an_action(self, clean_env):
        rep = pf.run_preflight()
        blocking = {
            pf.OPERATOR_ACTION, pf.PRIVILEGED_HOST,
            pf.EXTERNAL_EVIDENCE, pf.AUTHORIZATION,
        }
        for f in rep["findings"]:
            if f["category"] in blocking:
                assert f["action"], f"{f['id']} blocks without telling the operator what to do"

    def test_every_finding_has_a_detail(self, clean_env):
        rep = pf.run_preflight()
        assert all(f["detail"] for f in rep["findings"])


class TestEnvChecking:
    def test_present_env_is_ready(self, clean_env):
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        assert by["env.evaluator_procedure"]["category"] == pf.READY

    def test_missing_env_is_operator_action_with_action(self, clean_env):
        clean_env.delenv("SWARM_F2_EMIT_BUNDLE", raising=False)
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        assert by["env.emit_bundle"]["category"] == pf.OPERATOR_ACTION
        assert "SWARM_F2_EMIT_BUNDLE" in by["env.emit_bundle"]["action"]

    def test_evaluator_impl_digest_is_computed_when_a_real_file_exists(self, clean_env, tmp_path):
        impl = tmp_path / "impl.py"
        impl.write_bytes(b"# evaluator\n")
        clean_env.setenv("SWARM_F2_EVALUATOR_IMPL", str(impl))
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        assert by["env.evaluator_impl"]["status"] == "present"
        assert len(by["env.evaluator_impl"]["detail"].split()[1]) == 64

    def test_nonexistent_evaluator_impl_is_operator_action(self, clean_env, tmp_path):
        clean_env.setenv("SWARM_F2_EVALUATOR_IMPL", str(tmp_path / "absent.py"))
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        assert by["env.evaluator_impl"]["status"] == "missing"


class TestProvenanceReporting:
    def test_proven_links_are_implemented_not_external(self):
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        base = by["provenance.base_model"]
        if base["status"] == "PROVEN":
            assert base["category"] == pf.IMPLEMENTED

    def test_conversion_link_is_reported_unrecorded_with_a_remedy(self):
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        conv = by["provenance.conversion"]
        assert conv["status"] == "UNRECORDED"
        assert "timestamp" in conv["action"].lower()

    def test_report_never_claims_the_chain_is_complete(self):
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.verdict"]["status"] != "complete"


class TestAttestationVerification:
    def _att(self):
        denied = {
            ("tcp", "example.com", 80): (iso.OUTCOME_DENIED, "refused", ""),
            ("tcp", "api.github.com", 443): (iso.OUTCOME_DENIED, "refused", ""),
            ("tcp", "93.184.216.34", 80): (iso.OUTCOME_DENIED, "refused", ""),
            ("dns", "8.8.8.8", 53): (iso.OUTCOME_DENIED, "timed out", ""),
            ("tcp", "2606:4700:4700::1111", 443): (iso.OUTCOME_DENIED, "refused", ""),
            ("tcp", "127.0.0.1", 1): (iso.OUTCOME_PERMITTED, "ok", "127.0.0.1"),
            ("tcp", "127.0.0.1", 8000): (iso.OUTCOME_PERMITTED, "ok", "127.0.0.1"),
        }

        def probe(p, h, pt):
            return denied.get((p, h, pt), (iso.OUTCOME_UNKNOWN, "np", ""))

        return iso.run_egress_probes(
            probe=probe, loopback_targets=(("127.0.0.1", 1),),
            required_services=(("backend", "127.0.0.1", 8000),),
            dns_probe=lambda: (iso.OUTCOME_DENIED, "refused"),
            ipv6_available=lambda: True,
            interface_inventory=("Loopback", "Wi-Fi"),
            policy_identity="fw", policy_sha256="ab" * 32,
            policy_assertions={"proxy": "denied", "alternate_interface": "denied"},
            arm_id="a", rollout_id="r", workspace="w",
        )

    def test_verified_attestation_is_reported_ready(self, tmp_path):
        p = tmp_path / "att.json"
        p.write_bytes(iso.render_attestation(self._att()))
        rep = pf.run_preflight(attestation=p)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["evidence.q9_attestation"]["status"] == "verified"
        assert by["evidence.q9_attestation"]["category"] == pf.READY

    def test_broken_attestation_is_privileged_not_ready(self, tmp_path):
        p = tmp_path / "att.json"
        p.write_bytes(iso.render_attestation(self._att().__class__(
            **{**self._att().__dict__, "negative_control": iso.OUTCOME_UNKNOWN})))
        rep = pf.run_preflight(attestation=p)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["evidence.q9_attestation"]["status"] == "not_established"
        assert by["evidence.q9_attestation"]["category"] == pf.PRIVILEGED_HOST

    def test_malformed_attestation_fails_closed(self, tmp_path):
        p = tmp_path / "att.json"
        p.write_bytes(b'{"schema":"nope"}')
        rep = pf.run_preflight(attestation=p)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["evidence.q9_attestation"]["status"] == "invalid"


class TestRendering:
    def test_render_includes_every_finding(self, clean_env):
        rep = pf.run_preflight()
        text = pf.render(rep)
        for f in rep["findings"]:
            assert f["id"] in text

    def test_render_states_the_scope_caveat(self, clean_env):
        text = pf.render(pf.run_preflight())
        assert "not a claim that F2 is" in text

    def test_json_mode_is_valid_json(self, clean_env, capsys):
        pf.main(["--json"])
        out = capsys.readouterr().out
        json.loads(out)


class TestIsolationCliArgumentContract:
    """Hermetic: these validate the CLI's input contract only.

    The CLI's probe path performs REAL socket I/O by design -- it is an observer
    of the enforced policy -- so it is exercised by direct invocation rather
    than from a unit test, which keeps this suite deterministic and offline.
    """

    def test_cli_rejects_malformed_policy_assert(self, capsys):
        assert iso.main(["--out", "x.json", "--policy-assert", "bogus"]) == 2

    def test_cli_rejects_malformed_required_service(self, capsys):
        assert iso.main(["--out", "x.json", "--required-service", "bogus"]) == 2

    def test_cli_rejects_malformed_external(self, capsys):
        assert iso.main(["--out", "x.json", "--external", "bogus"]) == 2

    def test_cli_has_no_flag_to_declare_dimensions_denied(self):
        """A dimension may only be asserted through --policy-assert, which is
        recorded as an assertion. There is deliberately no blanket --denied flag."""
        import inspect

        src = inspect.getsource(iso.main)
        assert "--policy-assert" in src
        for forbidden in ('"--denied"', "'--denied'", '"--all-denied"'):
            assert forbidden not in src

    def _stub_network(self, monkeypatch, loopback_permitted=True):
        """Neutralise socket I/O without replacing the code under test.

        `run_egress_probes` stays real; only its network touch points are
        stubbed, so the CLI -> probe -> attestation -> verdict path is exercised
        end to end while staying offline and fast.
        """
        def probe(protocol, host, port):
            local = str(host) in ("127.0.0.1", "::1")
            if local:
                return (iso.OUTCOME_PERMITTED, "stub", "127.0.0.1") if loopback_permitted \
                    else (iso.OUTCOME_DENIED, "stub", "")
            return (iso.OUTCOME_DENIED, "stub winerror=10013", "")

        def _no_dns(*a, **k):
            raise OSError("stub: no dns")

        monkeypatch.setattr(iso, "default_egress_probe", probe)
        monkeypatch.setattr(iso.socket, "getaddrinfo", _no_dns)

    def test_cli_writes_the_file_atomically(self, tmp_path, monkeypatch):
        import json as _json

        self._stub_network(monkeypatch)
        out = tmp_path / "att.json"
        rc = iso.main([
            "--out", str(out), "--arm-id", "a", "--rollout-id", "r", "--workspace", "w",
            "--policy-identity", "fw", "--policy-sha256", "ab" * 32,
            "--policy-assert", "proxy=denied",
            "--policy-assert", "alternate_interface=denied",
            "--required-service", "backend=127.0.0.1:8000",
        ])
        assert out.is_file()
        assert not (tmp_path / (out.name + ".tmp")).exists()
        payload = _json.loads(out.read_text(encoding="utf-8"))
        assert payload["schema"] == iso.SCHEMA
        assert payload["arm_id"] == "a" and payload["rollout_id"] == "r"
        assert payload["observer_pid"] > 0
        # DNS could not resolve under the stub, so isolation must not be claimed.
        assert rc == 1

    def test_policy_assertion_is_recorded_as_an_assertion_not_an_observation(self, tmp_path, monkeypatch):
        import json as _json

        self._stub_network(monkeypatch)
        out = tmp_path / "att.json"
        iso.main([
            "--out", str(out), "--policy-assert", "proxy=denied",
            "--policy-identity", "", "--policy-sha256", "",
        ])
        payload = _json.loads(out.read_text(encoding="utf-8"))
        assert payload["policy_assertions"][0]["dimension"] == "proxy"
        assert payload["policy_assertions"][0]["source"] == "enforced_policy"
        proxy = [p for p in payload["probes"] if p["dimension"] == "proxy"]
        assert proxy and proxy[0]["outcome"] == iso.OUTCOME_UNKNOWN

    def test_anonymous_policy_assertion_never_satisfies_a_dimension(self, tmp_path, monkeypatch):
        """An assertion with no identified policy must not establish a dimension."""
        self._stub_network(monkeypatch)
        out = tmp_path / "att.json"
        iso.main([
            "--out", str(out), "--arm-id", "a", "--rollout-id", "r", "--workspace", "w",
            "--policy-assert", "proxy=denied",
            "--policy-assert", "alternate_interface=denied",
            "--policy-identity", "", "--policy-sha256", "",
        ])
        v = iso.verify_isolation_attestation(
            iso.parse_attestation(out.read_bytes()))
        assert "proxy" not in v.policy_declared_dimensions
        assert "proxy" in v.unproven_dimensions
