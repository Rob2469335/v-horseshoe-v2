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
        "SWARM_S8_ENVIRONMENT_IDENTITY", "SWARM_S8_BASE_STATE_DIGEST",
        "SWARM_S8_GOLD_STATE_DIGEST",
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
    monkeypatch.setenv("SWARM_S8_ENVIRONMENT_IDENTITY", "s8-test-env")
    monkeypatch.setenv("SWARM_S8_BASE_STATE_DIGEST", "b" * 64)
    monkeypatch.setenv("SWARM_S8_GOLD_STATE_DIGEST", "c" * 64)
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
        assert by["population.admission"]["status"] == "insufficient"

    def test_population_acquisition_is_authorized_not_authz_blocked(self):
        # F2-IMPL-AUTH-028/029/031 record population acquisition, so preflight must
        # not claim it is unauthorized; admission stays blocked by missing S8
        # evidence, so preflight must still be blocked (never READY from this).
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        assert "authz.population_acquisition" not in by
        assert by["population.admission"]["category"] == pf.EXTERNAL_EVIDENCE
        assert by["population.admission"]["status"] != "present"
        assert by["evidence.s8_base_gold"]["status"] == "absent"
        assert rep["blocking_count"] > 0

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


class TestS8EnvChecking:
    """The three S8-only governed inputs, reported fail-closed by preflight.

    They have no F2 fallback, so ``f2_s8_compose.load_governance_from_env``
    refuses without them.  Preflight must surface them rather than let the
    operator discover them only at launch -- but a green S8 finding is READINESS,
    never authorization to execute.
    """

    S8_VARS = (
        "SWARM_S8_ENVIRONMENT_IDENTITY",
        "SWARM_S8_BASE_STATE_DIGEST",
        "SWARM_S8_GOLD_STATE_DIGEST",
    )

    def test_reported_names_are_the_authoritative_s8_only_set(self):
        from qwen_train.f2_s8_compose import S8_ENV_FALLBACKS, S8_GOVERNANCE_ENV

        expected = {
            "env.s8_" + name.removeprefix("SWARM_S8_").lower()
            for name, _ in S8_GOVERNANCE_ENV if name not in S8_ENV_FALLBACKS
        }
        rep = pf.run_preflight()
        reported = {f["id"] for f in rep["findings"] if f["id"].startswith("env.s8_")}
        assert reported == expected
        # The six F2-fallback inputs are NOT re-reported here.
        assert len(expected) == 3

    def test_all_three_are_ready_when_provisioned(self, clean_env):
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        for name in self.S8_VARS:
            ident = "env.s8_" + name.removeprefix("SWARM_S8_").lower()
            assert by[ident]["category"] == pf.READY, name

    def test_an_absent_input_is_operator_action_with_an_action(self, clean_env):
        clean_env.delenv("SWARM_S8_ENVIRONMENT_IDENTITY", raising=False)
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        f = by["env.s8_environment_identity"]
        assert f["category"] == pf.OPERATOR_ACTION
        assert f["status"] == "absent"
        assert "SWARM_S8_ENVIRONMENT_IDENTITY" in f["action"]

    def test_a_malformed_digest_is_blocking_not_accepted(self, clean_env):
        clean_env.setenv("SWARM_S8_BASE_STATE_DIGEST", "not-a-sha256")
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        f = by["env.s8_base_state_digest"]
        assert f["category"] == pf.OPERATOR_ACTION
        assert f["status"] == "malformed"

    def test_identical_state_digests_are_blocking(self, clean_env):
        clean_env.setenv("SWARM_S8_GOLD_STATE_DIGEST", "b" * 64)  # == base
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        assert by["env.s8_gold_state_digest"]["category"] == pf.OPERATOR_ACTION
        assert by["env.s8_gold_state_digest"]["status"] == "identical"

    def test_no_s8_value_is_ever_reported(self, clean_env):
        secretish = "s8-identity-do-not-print-1234567890"
        clean_env.setenv("SWARM_S8_ENVIRONMENT_IDENTITY", secretish)
        blob = json.dumps(pf.run_preflight())
        assert secretish not in blob
        assert "value not printed" in blob

    def test_s8_findings_are_readiness_never_authorization(self, clean_env):
        rep = pf.run_preflight()
        cats = {f["category"] for f in rep["findings"] if f["id"].startswith("env.s8_")}
        assert cats <= {pf.READY, pf.OPERATOR_ACTION}
        assert pf.AUTHORIZATION not in cats


class TestEnvControlsFinding:
    """``env.controls`` reports the value the F2 child will ACTUALLY receive.

    The child is spawned with the adapter-forced environment and then runs
    ``load_dotenv(override=True)`` (``swarm_os/app/main.py:20``), so a ``.env``
    entry overrides the adapter. These tests pin that precedence, pin the
    fail-closed outcomes, and pin that credential-bearing names in ``.env``
    never reach the finding.
    """

    _BLOCKING_CATEGORIES = (pf.OPERATOR_ACTION, pf.PRIVILEGED_HOST,
                            pf.EXTERNAL_EVIDENCE, pf.AUTHORIZATION)

    @pytest.fixture(autouse=True)
    def isolate_controls(self, monkeypatch):
        """No control may be inherited from the ambient process environment."""
        for name in tuple(pf.F2_REQUIRED_CONTROLS):
            monkeypatch.delenv(name, raising=False)
        yield monkeypatch

    @staticmethod
    def _write_env(tmp_path, text):
        (tmp_path / ".env").write_text(text, encoding="utf-8")

    @staticmethod
    def _finding(report):
        return {f["id"]: f for f in report["findings"]}["env.controls"]

    def test_dotenv_carrying_every_required_value_passes(self, tmp_path):
        self._write_env(tmp_path, "".join(
            f"{name}={value}\n"
            for name, value in pf.F2_REQUIRED_CONTROLS.items()
        ))
        f = self._finding(pf.run_preflight(root=tmp_path))
        assert f["category"] == pf.READY
        assert f["status"] == "match"
        assert f["action"] == ""
        for name in pf.F2_REQUIRED_CONTROLS:
            assert name in f["detail"]

    def test_dotenv_contradicting_the_adapter_is_blocking(self, tmp_path):
        # The adapter forces 0; .env says 1. The child loads .env with
        # override=True AFTER the adapter, so the effective value is 1.
        assert pf.F2_ADAPTER_CONTROLS["SWARM_AUTONOMY"] == "0"
        self._write_env(tmp_path, "SWARM_AUTONOMY=1\n")

        f = self._finding(pf.run_preflight(root=tmp_path))

        assert f["category"] == pf.OPERATOR_ACTION
        assert f["category"] in self._BLOCKING_CATEGORIES
        assert f["status"] == "mismatch"
        assert "SWARM_AUTONOMY=1 (from .env) but required 0" in f["detail"]
        assert f["action"]

    def test_invalid_value_is_blocking(self, tmp_path):
        self._write_env(tmp_path, "SWARM_AUTONOMY=garbage\n")

        f = self._finding(pf.run_preflight(root=tmp_path))

        assert f["category"] == pf.OPERATOR_ACTION
        assert f["category"] in self._BLOCKING_CATEGORIES
        assert f["status"] == "invalid"
        assert "SWARM_AUTONOMY=garbage" in f["detail"]

    def test_absent_from_dotenv_but_forced_by_the_adapter_passes(self, tmp_path):
        self._write_env(tmp_path, "UNRELATED_THING=1\n")

        f = self._finding(pf.run_preflight(root=tmp_path))

        assert f["category"] == pf.READY
        assert f["status"] == "match"
        assert "from the F2 adapter" not in f["detail"]

    def test_absent_from_dotenv_and_adapter_is_blocking(self, tmp_path):
        self._write_env(tmp_path, "UNRELATED_THING=1\n")

        f = self._finding(pf.run_preflight(root=tmp_path, adapter_controls={}))

        assert f["category"] == pf.OPERATOR_ACTION
        assert f["category"] in self._BLOCKING_CATEGORIES
        assert f["status"] == "missing"
        assert "absent from .env, the F2 adapter and the process environment" in f["detail"]

    def test_credential_bearing_names_are_never_read_into_the_finding(self, tmp_path):
        sentinel = "sk-SENTINEL-MUST-NEVER-APPEAR"
        self._write_env(tmp_path, "".join(
            f"{name}={value}\n"
            for name, value in pf.F2_REQUIRED_CONTROLS.items()
        ) + f"OPENAI_API_KEY={sentinel}\n")

        report = pf.run_preflight(root=tmp_path)
        f = self._finding(report)

        assert f["category"] == pf.READY
        assert sentinel not in json.dumps(report)
        assert "OPENAI_API_KEY" not in json.dumps(report)
        assert "OPENAI_API_KEY" not in f["detail"]
        # Exactly the seven controls are named -- nothing else from .env is.
        import re

        assert set(re.findall(r"(SWARM_[A-Z0-9_]+)=", f["detail"])) == set(
            pf.F2_REQUIRED_CONTROLS
        )

    def test_value_check_blocks_where_a_presence_only_check_would_pass(self, tmp_path, monkeypatch):
        """Regression against presence-only reporting.

        Every control is present in the process environment at its required
        value, so the old "the name exists" rule reports satisfied -- yet .env
        forces SWARM_AUTONOMY to 1, which the child will actually receive.
        """
        for name, required in pf.F2_REQUIRED_CONTROLS.items():
            monkeypatch.setenv(name, required)
        self._write_env(tmp_path, "SWARM_AUTONOMY=1\n")

        # The old presence-only rule, expressed exactly as it was evaluated:
        # a non-empty environment value counted as satisfied.
        presence_only_would_pass = all(
            pf._env(name) for name in pf.F2_REQUIRED_CONTROLS
        )
        assert presence_only_would_pass

        f = self._finding(pf.run_preflight(root=tmp_path))

        assert f["category"] != pf.READY
        assert f["category"] == pf.OPERATOR_ACTION
        assert "SWARM_AUTONOMY=1 (from .env) but required 0" in f["detail"]


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

    def test_preflight_accepts_conversion_record_dict_incomplete_fails_closed(self):
        # Incomplete conversion record still reports UNRECORDED
        rep = pf.run_preflight(conversion_record={"source_base": "Qwen"})
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "UNRECORDED"
        assert by["provenance.verdict"]["status"] != "complete"

    def test_preflight_accepts_valid_conversion_record_file(self, tmp_path):
        from qwen_train import f2_model_provenance as mp
        # Assemble compliant conversion record bound to real proven artifacts
        merged = tmp_path / "Robs4B_Merged_Hf.safetensors"
        merged.write_bytes(b"merged_weights" * 100)
        adapter_dir = pf.Path(__file__).resolve().parents[1] / "qwen_train" / "robs4b_final_adapter"
        gguf_path = pf.SERVED_GGUF_PATH
        if not (adapter_dir / "adapter_config.json").is_file() or not gguf_path.is_file():
            pytest.skip("requires host-local robs4b artifacts")

        chain0 = mp.build_conversion_chain(
            model_alias="robs4b",
            training_runs_path=pf.Path(__file__).resolve().parents[1] / "qwen_train" / "training_runs.jsonl",
            adapter_dirs=[adapter_dir],
            corpus_search_roots=[],
            served_gguf=None,
        )
        real_base_digest = chain0.link(mp.LINK_BASE).digest
        rec = mp.derive_conversion_record(
            source_adapter=adapter_dir,
            merged_artifact=merged,
            served_gguf=gguf_path,
            source_base_sha256=real_base_digest,
            conversion_tool="convert_hf_to_gguf.py",
            conversion_tool_version="c0bc859",
            merge_operation="merge.py --adapter robs4b_final_adapter --base Qwen3.5-4B",
            operator="operator@host",
            conversion_inputs=[str(merged), str(gguf_path)],
        )
        rec_file = tmp_path / "conversion_record.json"
        rec_file.write_text(json.dumps(rec), encoding="utf-8")

        rep = pf.run_preflight(conversion_record=rec_file)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "PROVEN"
        assert by["provenance.conversion"]["category"] == pf.IMPLEMENTED
        assert by["provenance.verdict"]["status"] == "complete"

    def test_preflight_conversion_record_mismatch_fails_closed(self, tmp_path):
        from qwen_train import f2_model_provenance as mp
        merged = tmp_path / "Robs4B_Merged_Hf.safetensors"
        merged.write_bytes(b"merged_weights" * 100)
        adapter_dir = pf.Path(__file__).resolve().parents[1] / "qwen_train" / "robs4b_final_adapter"
        gguf_path = pf.SERVED_GGUF_PATH
        if not (adapter_dir / "adapter_config.json").is_file() or not gguf_path.is_file():
            pytest.skip("requires host-local robs4b artifacts")

        rec = mp.derive_conversion_record(
            source_adapter=adapter_dir,
            merged_artifact=merged,
            served_gguf=gguf_path,
            source_base_sha256="wrong_base_digest_00000000",
            conversion_tool="convert_hf_to_gguf.py",
            conversion_tool_version="c0bc859",
            merge_operation="merge.py",
            operator="operator@host",
            conversion_inputs=[str(merged)],
        )
        rep = pf.run_preflight(conversion_record=rec)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "MISMATCH"
        assert by["provenance.verdict"]["status"] != "complete"

    def test_preflight_conversion_record_nonexistent_path_fails_closed(self, tmp_path):
        missing = tmp_path / "nonexistent_record.json"
        rep = pf.run_preflight(conversion_record=missing)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "error"
        assert "does not exist" in by["provenance.conversion"]["detail"]
        assert by["provenance.conversion"]["category"] == pf.EXTERNAL_EVIDENCE
        assert by["provenance.verdict"]["status"] == "incomplete"

    def test_preflight_conversion_record_invalid_json_fails_closed(self, tmp_path):
        bad = tmp_path / "bad_record.json"
        bad.write_text("{not valid json: ", encoding="utf-8")
        rep = pf.run_preflight(conversion_record=bad)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "error"
        assert "could not be parsed" in by["provenance.conversion"]["detail"]
        assert by["provenance.verdict"]["status"] == "incomplete"

    def test_preflight_conversion_record_non_mapping_fails_closed(self, tmp_path):
        bad = tmp_path / "list_record.json"
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        rep = pf.run_preflight(conversion_record=bad)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "error"
        assert "is not a JSON object" in by["provenance.conversion"]["detail"]
        assert by["provenance.verdict"]["status"] == "incomplete"

    def test_preflight_conversion_record_cli_precedence_over_env(self, tmp_path, monkeypatch):
        # Env points to invalid record, but CLI/explicit arg points to valid record.
        # Explicit arg MUST take precedence.
        from qwen_train import f2_model_provenance as mp
        merged = tmp_path / "Robs4B_Merged_Hf.safetensors"
        merged.write_bytes(b"merged_weights" * 100)
        adapter_dir = pf.Path(__file__).resolve().parents[1] / "qwen_train" / "robs4b_final_adapter"
        gguf_path = pf.SERVED_GGUF_PATH
        if not (adapter_dir / "adapter_config.json").is_file() or not gguf_path.is_file():
            pytest.skip("requires host-local robs4b artifacts")

        chain0 = mp.build_conversion_chain(
            model_alias="robs4b",
            training_runs_path=pf.Path(__file__).resolve().parents[1] / "qwen_train" / "training_runs.jsonl",
            adapter_dirs=[adapter_dir],
            corpus_search_roots=[],
            served_gguf=None,
        )
        real_base_digest = chain0.link(mp.LINK_BASE).digest
        rec = mp.derive_conversion_record(
            source_adapter=adapter_dir,
            merged_artifact=merged,
            served_gguf=gguf_path,
            source_base_sha256=real_base_digest,
            conversion_tool="convert_hf_to_gguf.py",
            conversion_tool_version="c0bc859",
            merge_operation="merge.py",
            operator="operator@host",
            conversion_inputs=[str(merged), str(gguf_path)],
        )
        good_file = tmp_path / "good.json"
        good_file.write_text(json.dumps(rec), encoding="utf-8")

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{bad", encoding="utf-8")

        monkeypatch.setenv("SWARM_F2_CONVERSION_RECORD", str(bad_file))
        # Passing good_file explicitly overrides the bad env var
        rep = pf.run_preflight(conversion_record=good_file)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "PROVEN"
        assert by["provenance.verdict"]["status"] == "complete"

    def test_preflight_conversion_record_env_fallback_valid(self, tmp_path, monkeypatch):
        from qwen_train import f2_model_provenance as mp
        merged = tmp_path / "Robs4B_Merged_Hf.safetensors"
        merged.write_bytes(b"merged_weights" * 100)
        adapter_dir = pf.Path(__file__).resolve().parents[1] / "qwen_train" / "robs4b_final_adapter"
        gguf_path = pf.SERVED_GGUF_PATH
        if not (adapter_dir / "adapter_config.json").is_file() or not gguf_path.is_file():
            pytest.skip("requires host-local robs4b artifacts")

        chain0 = mp.build_conversion_chain(
            model_alias="robs4b",
            training_runs_path=pf.Path(__file__).resolve().parents[1] / "qwen_train" / "training_runs.jsonl",
            adapter_dirs=[adapter_dir],
            corpus_search_roots=[],
            served_gguf=None,
        )
        real_base_digest = chain0.link(mp.LINK_BASE).digest
        rec = mp.derive_conversion_record(
            source_adapter=adapter_dir,
            merged_artifact=merged,
            served_gguf=gguf_path,
            source_base_sha256=real_base_digest,
            conversion_tool="convert_hf_to_gguf.py",
            conversion_tool_version="c0bc859",
            merge_operation="merge.py",
            operator="operator@host",
            conversion_inputs=[str(merged), str(gguf_path)],
        )
        rec_file = tmp_path / "env_record.json"
        rec_file.write_text(json.dumps(rec), encoding="utf-8")

        monkeypatch.setenv("SWARM_F2_CONVERSION_RECORD", str(rec_file))
        rep = pf.run_preflight(conversion_record=None)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "PROVEN"
        assert by["provenance.verdict"]["status"] == "complete"

    def test_preflight_conversion_record_env_fallback_invalid_fails_closed(self, tmp_path, monkeypatch):
        bad_file = tmp_path / "env_bad.json"
        bad_file.write_text("{bad", encoding="utf-8")
        monkeypatch.setenv("SWARM_F2_CONVERSION_RECORD", str(bad_file))
        rep = pf.run_preflight(conversion_record=None)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "error"
        assert "could not be parsed" in by["provenance.conversion"]["detail"]
        assert by["provenance.verdict"]["status"] == "incomplete"



class TestAttestationVerification:
    def _att(self):
        denied = {
            ("tcp", "93.184.216.34", 80): (iso.OUTCOME_DENIED, "refused", ""),
            ("tcp", "93.184.216.34", 443): (iso.OUTCOME_DENIED, "refused", ""),
            ("tcp", "93.184.216.34", 8080): (iso.OUTCOME_DENIED, "refused", ""),
            ("dns", "8.8.8.8", 53): (iso.OUTCOME_DENIED, "winerror=10013", ""),
            ("tcp", "2606:4700:4700::1111", 443): (iso.OUTCOME_DENIED, "refused", ""),
            ("tcp", "127.0.0.1", 1): (iso.OUTCOME_PERMITTED, "ok", "127.0.0.1"),
            ("tcp", "127.0.0.1", 8000): (iso.OUTCOME_PERMITTED, "ok", "127.0.0.1"),
        }

        def probe(p, h, pt):
            return denied.get((p, h, pt), (iso.OUTCOME_UNKNOWN, "np", ""))

        return iso.run_egress_probes(
            probe=probe,
            udp_probe=lambda h, pt: denied.get(("dns", h, pt), (iso.OUTCOME_UNKNOWN, "np", "")),
            loopback_targets=(("127.0.0.1", 1),),
            required_services=(("backend", "127.0.0.1", 8000),),
            dns_probe=lambda: (iso.OUTCOME_DENIED, "refused"),
            ipv6_available=lambda: True,
            interface_inventory=("Loopback", "Wi-Fi"),
            policy_identity="fw", policy_sha256="ab" * 32,
            policy_assertions={"proxy": "denied", "alternate_interface": "denied"},
            arm_id="a", rollout_id="r", workspace="w",
            # The enforcement identity is part of THE SAME contract the consuming
            # gate applies (qwen_train.f2_readiness._check_no_egress): a
            # satisfied verdict alone is unattributable. Preflight reconciles the
            # two Q9 gates against that contract, so a fully-valid attestation
            # carries it.
            enforcement_scope="vm",
            executing_user="F2VM\\user",
            interpreter_path=r"C:\f2\python.exe",
            interpreter_sha256="cd" * 32,
        )

    @staticmethod
    def _count(report, ident):
        return sum(1 for f in report["findings"] if f["id"] == ident)

    def test_q9_findings_are_emitted_exactly_once(self, tmp_path):
        """The duplicate that made the same id both 'not_supplied' and
        'verified' must never return."""
        p = tmp_path / "att.json"
        p.write_bytes(iso.render_attestation(self._att()))
        for rep in (pf.run_preflight(), pf.run_preflight(attestation=p)):
            assert self._count(rep, "evidence.q9_attestation") == 1
            assert self._count(rep, "host.egress_enforcement") == 1

    def test_absent_attestation_blocks_both_q9_gates(self):
        rep = pf.run_preflight()
        by = {f["id"]: f for f in rep["findings"]}
        assert by["host.egress_enforcement"]["status"] == "not_enforced"
        assert by["host.egress_enforcement"]["category"] == pf.PRIVILEGED_HOST
        assert by["evidence.q9_attestation"]["status"] == "not_supplied"
        assert by["evidence.q9_attestation"]["category"] == pf.EXTERNAL_EVIDENCE

    def test_valid_attestation_clears_only_the_q9_gates(self, tmp_path):
        p = tmp_path / "att.json"
        p.write_bytes(iso.render_attestation(self._att()))
        base = pf.run_preflight()
        rep = pf.run_preflight(attestation=p)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["host.egress_enforcement"]["status"] == "enforced"
        assert by["host.egress_enforcement"]["category"] == pf.READY
        assert by["evidence.q9_attestation"]["status"] == "verified"
        assert by["evidence.q9_attestation"]["category"] == pf.READY

        b0 = {f["id"]: f["status"] for f in base["findings"]}
        b1 = {f["id"]: f["status"] for f in rep["findings"]}
        changed = {k for k in b1 if b0.get(k) != b1.get(k)}
        assert changed == {"host.egress_enforcement", "evidence.q9_attestation"}

        # Unrelated blockers are untouched, and the two Q9 gates were the only
        # blocking findings that cleared.
        assert by["evidence.s8_base_gold"]["category"] == pf.EXTERNAL_EVIDENCE
        assert by["evidence.active_lesson"]["category"] == pf.EXTERNAL_EVIDENCE
        assert by["population.admission"]["category"] == pf.EXTERNAL_EVIDENCE
        assert rep["blocking_count"] == base["blocking_count"] - 2

    def test_missing_enforcement_identity_does_not_verify(self, tmp_path):
        att = self._att()
        stripped = att.__class__(**{**att.__dict__,
            "enforcement_scope": "", "interpreter_path": "", "interpreter_sha256": ""})
        p = tmp_path / "att.json"
        p.write_bytes(iso.render_attestation(stripped))
        rep = pf.run_preflight(attestation=p)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["evidence.q9_attestation"]["status"] == "not_established"
        assert by["evidence.q9_attestation"]["category"] == pf.PRIVILEGED_HOST
        assert by["host.egress_enforcement"]["status"] == "not_enforced"
        assert "ENFORCEMENT IDENTITY" in by["evidence.q9_attestation"]["detail"]

    def test_missing_policy_binding_does_not_verify(self, tmp_path):
        att = self._att()
        stripped = att.__class__(**{**att.__dict__,
            "policy_identity": "", "policy_sha256": ""})
        p = tmp_path / "att.json"
        p.write_bytes(iso.render_attestation(stripped))
        rep = pf.run_preflight(attestation=p)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["evidence.q9_attestation"]["status"] == "not_established"
        assert by["host.egress_enforcement"]["status"] == "not_enforced"

    def test_reachable_egress_does_not_verify(self, tmp_path):
        def probe(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "ok", "127.0.0.1")
            return (iso.OUTCOME_PERMITTED, "connected", "10.0.0.5")

        att = iso.run_egress_probes(
            probe=probe, loopback_targets=(("127.0.0.1", 1),),
            required_services=(("backend", "127.0.0.1", 8000),),
            dns_probe=lambda: (iso.OUTCOME_DENIED, "refused"),
            ipv6_available=lambda: True,
            policy_identity="fw", policy_sha256="ab" * 32,
            policy_assertions={"proxy": "denied", "alternate_interface": "denied"},
            arm_id="a", rollout_id="r", workspace="w",
            enforcement_scope="vm",
            interpreter_path=r"C:\f2\python.exe", interpreter_sha256="cd" * 32,
        )
        p = tmp_path / "att.json"
        p.write_bytes(iso.render_attestation(att))
        rep = pf.run_preflight(attestation=p)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["evidence.q9_attestation"]["status"] == "not_established"
        assert by["host.egress_enforcement"]["status"] == "not_enforced"

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


class TestEnforcementCapturePassThrough:
    """Preflight must verify Q9 exactly the way the consuming gate does.

    `qwen_train.f2_readiness._check_no_egress` takes the HOST enforcement
    capture, because a silently-dropping control (a Hyper-V extended ACL drops
    in the vSwitch) produces a timeout no observer can tell from a blackhole.
    `_q9_findings` never passed it, so the command the operator handoff tells
    the operator to run reported `http/https/tcp/udp` as `unproven` for an
    attestation whose real verdict has only `ipv6` unproven -- the
    documentation-vs-implementation contradiction on the gate that decides
    readiness.

    The capture changes no contract: it must still hash to the digest the
    attestation claims, its effective highest-weight rule must still derive an
    explicit `Deny`, the enforcement identity must still be bound, and the gate
    still cannot clear while any dimension is unproven.
    """

    @staticmethod
    def _attestation_and_capture():
        from qwen_train import f2_isolation as iso

        capture = {
            "schema": iso.ENFORCEMENT_CAPTURE_SCHEMA,
            "kind": "hyper-v-extended-acl",
            "subject": {"vm_name": "F2-S8-VM1", "vm_id": "c" * 32,
                        "adapter_mac": "00155D56D603"},
            "captured_at": "2026-10-14T00:00:00Z",
            "rules": [
                {"direction": "outbound", "action": "deny", "remote_ip": "0.0.0.0/0",
                 "protocol": "any", "weight": 350, "stateful": False},
                {"direction": "outbound", "action": "deny", "remote_ip": "ANY",
                 "protocol": "any", "weight": 1, "stateful": False},
            ],
        }

        def probe(protocol, host, port):
            if str(host) == "127.0.0.1":
                return (iso.OUTCOME_PERMITTED, "connected", "127.0.0.1")
            return ("timeout", "timed out (silently dropped)", "")

        att = iso.run_egress_probes(
            probe=probe,
            udp_probe=lambda h, p: ("silent", "sent; no response", ""),
            loopback_targets=(("127.0.0.1", 1),),
            required_services=(("channel", "127.0.0.1", 2179),),
            dns_probe=lambda: ("timeout", "timed out"),
            interface_inventory=("Ethernet 2",),
            policy_identity="hyper-v-extended-acl:F2-S8-VM1",
            policy_sha256="ab" * 32,
            policy_assertions={"proxy": "denied", "alternate_interface": "denied"},
            external_targets=(("1.1.1.1", 443), ("93.184.216.34", 80),
                              ("93.184.216.34", 8080)),
            udp_target=("8.8.8.8", 53),
            arm_id="s8-f2-vm1",
            rollout_id="s8-pilot",
            workspace="C:/f2_s8",
            # The real S8 guest: link-local IPv6 only, so the dimension is
            # `unavailable` and stays unproven -- the one thing the capture
            # cannot and must not stand in for.
            ipv6_available=lambda: False,
            enforcement_scope="vm",
            executing_user="DESKTOP-H6A0HQS\\rob3",
            interpreter_path=r"C:\f2_tooling\python\python.exe",
            interpreter_sha256="cd" * 32,
            enforcement_capture_sha256=iso.canonical_enforcement_digest(capture),
        )
        return att, capture

    def _paths(self, tmp_path):
        att, capture = self._attestation_and_capture()
        att_path = tmp_path / "att.json"
        att_path.write_bytes(iso.render_attestation(att))
        capture_path = tmp_path / "capture.json"
        capture_path.write_text(json.dumps(capture), encoding="utf-8")
        return att_path, capture_path

    @staticmethod
    def _by_id(report):
        return {f["id"]: f for f in report["findings"]}

    def _unproven(self, report):
        import re

        detail = self._by_id(report)["evidence.q9_attestation"]["detail"]
        m = re.search(r"UNPROVEN: \[([^\]]*)\]", detail)
        assert m, detail
        return m.group(1).replace("'", "").split(", ")

    def test_without_the_capture_the_drop_dimensions_stay_unproven(self, tmp_path):
        """The defect this wiring fixes, pinned as a regression."""
        att_path, _ = self._paths(tmp_path)
        rep = pf.run_preflight(attestation=att_path)
        assert {"http", "https", "tcp", "udp"} <= set(self._unproven(rep))

    def test_with_the_capture_only_ipv6_remains_and_the_gate_still_blocks(
        self, tmp_path
    ):
        att_path, capture_path = self._paths(tmp_path)
        rep = pf.run_preflight(attestation=att_path,
                               enforcement_capture=capture_path)
        by = self._by_id(rep)
        detail = by["evidence.q9_attestation"]["detail"]
        # The unproven set shrinks to the one thing only an owner decision can
        # resolve; the capture itself is verified, not assumed.
        assert self._unproven(rep) == ["ipv6"]
        assert "enforcement capture accepted" in detail
        # Narrowing the REASON must not clear the gate: no dimension may stay
        # unproven, so both Q9 findings remain blocking.
        assert by["evidence.q9_attestation"]["status"] == "not_established"
        assert by["evidence.q9_attestation"]["category"] == pf.PRIVILEGED_HOST
        assert by["host.egress_enforcement"]["status"] == "not_enforced"
        assert by["host.egress_enforcement"]["category"] == pf.PRIVILEGED_HOST
        assert rep["result"] == "BLOCKED"

    def test_only_the_two_q9_findings_are_affected(self, tmp_path):
        att_path, capture_path = self._paths(tmp_path)
        without = self._by_id(pf.run_preflight(attestation=att_path))
        with_cap = self._by_id(
            pf.run_preflight(attestation=att_path, enforcement_capture=capture_path)
        )
        changed = {k for k in with_cap if with_cap[k] != without.get(k)}
        assert changed <= {"host.egress_enforcement", "evidence.q9_attestation"}
        # Every other gate keeps its own status and category.
        for k in set(without) - {"host.egress_enforcement",
                                 "evidence.q9_attestation"}:
            assert with_cap[k] == without[k]

    def test_a_capture_that_does_not_match_the_claim_changes_nothing(self, tmp_path):
        """A substituted capture must not be able to unlock the gate."""
        att, capture = self._attestation_and_capture()
        att_path = tmp_path / "att.json"
        att_path.write_bytes(iso.render_attestation(att))
        other = dict(capture, rules=[{"direction": "outbound", "action": "allow",
                                      "remote_ip": "0.0.0.0/0", "protocol": "any",
                                      "weight": 999, "stateful": True}])
        capture_path = tmp_path / "other.json"
        capture_path.write_text(json.dumps(other), encoding="utf-8")
        rep = pf.run_preflight(attestation=att_path, enforcement_capture=capture_path)
        assert {"http", "https", "tcp", "udp"} <= set(self._unproven(rep))
        by = self._by_id(rep)
        assert by["host.egress_enforcement"]["status"] == "not_enforced"

    def test_an_unreadable_capture_is_reported_not_silently_ignored(self, tmp_path):
        att_path, _ = self._paths(tmp_path)
        missing = tmp_path / "never-written.json"
        rep = pf.run_preflight(attestation=att_path, enforcement_capture=missing)
        by = self._by_id(rep)
        assert by["host.egress_enforcement"]["status"] == "not_enforced"
        assert by["evidence.q9_attestation"]["status"] == "not_established"
        assert "could not be read" in by["host.egress_enforcement"]["detail"]
        assert "capture unusable" in by["evidence.q9_attestation"]["detail"]
        # An input the operator named must not be silently downgraded to
        # "no capture given": the attestation itself is fine here.
        assert by["evidence.q9_attestation"]["status"] != "invalid"

    def test_a_capture_without_an_attestation_is_a_usage_error(self, capsys):
        assert pf.main(["--enforcement-capture", "capture.json"]) == 2
        assert "--enforcement-capture requires --attestation" in capsys.readouterr().err

    def test_capture_without_attestation_leaves_the_absent_evidence_block(
        self, tmp_path
    ):
        _, capture_path = self._paths(tmp_path)
        rep = pf.run_preflight(enforcement_capture=capture_path)
        by = self._by_id(rep)
        assert by["evidence.q9_attestation"]["status"] == "not_supplied"
        assert by["host.egress_enforcement"]["status"] == "not_enforced"


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


class TestOptionBModelIdentity:
    """Option B (2026-10-14): adopted the witnessed reconversion as the F2
    distiller identity. The two artifacts are DISTINCT and must never be
    conflated; the original is preserved.
    """

    ORIGINAL_SHA256 = "65202f372110dde854b40ce15dcd1b6ab56a1fe9ea542b84b6a9cc745b242d41"
    RECONVERTED_SHA256 = "3e5ba031f0b50e98450d1575aa19780bc5b4b13f48bf3eca38f3c915a442a337"

    def test_served_identity_points_at_the_witnessed_reconversion(self):
        assert pf.SERVED_GGUF_SHA256 == self.RECONVERTED_SHA256
        assert pf.SERVED_GGUF_PATH.name == "robs4b_q4km_reconverted.gguf"

    def test_original_and_reconverted_are_distinct(self):
        # The adopted digest is NOT the previously served digest.
        assert pf.SERVED_GGUF_SHA256 != self.ORIGINAL_SHA256
        assert pf.SERVED_GGUF_PATH.name != "robs4b_q4km.gguf"

    def test_original_served_artifact_is_preserved(self):
        original = pf.Path(__file__).resolve().parents[1] / "qwen_train" / "robs4b_q4km.gguf"
        if not original.is_file():
            pytest.skip("original served artifact not present on this host")
        import hashlib
        h = hashlib.sha256()
        with open(original, "rb") as fh:
            for b in iter(lambda: fh.read(1 << 20), b""):
                h.update(b)
        assert h.hexdigest() == self.ORIGINAL_SHA256

    def test_record_naming_the_original_digest_fails_closed(self, tmp_path):
        # A record whose served_gguf_sha256 names the ORIGINAL artifact must NOT
        # be accepted for the adopted reconversion: MISMATCH, fail closed.
        rec = {
            "source_base": "Qwen3.5-4B",
            "source_base_sha256": "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
            "source_adapter": "robs4b_final_adapter",
            "source_adapter_sha256": "294feecc592b5a7abca7913dcb58ea797e62c2e3fe3a42abf3fe5e59db506cfd",
            "merge_operation": "merge",
            "conversion_tool": "llama.cpp",
            "conversion_tool_version": "b11624",
            "conversion_inputs": ["a", "b"],
            "merged_artifact": "Robs4B_Merged_Hf",
            "merged_artifact_sha256": "f" * 64,
            "served_gguf": "robs4b_q4km.gguf",
            "served_gguf_sha256": self.ORIGINAL_SHA256,
            "operator": "test",
        }
        p = tmp_path / "rec.json"
        p.write_text(json.dumps(rec), encoding="utf-8")
        rep = pf.run_preflight(conversion_record=p)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "MISMATCH"
        assert by["provenance.verdict"]["status"] != "complete"

    def test_real_reconversion_record_accepts(self):
        rec_path = pf.Path(r"C:\Users\rober\F2_EVIDENCE\s8\conversion\conversion_record.json")
        if not (rec_path.is_file() and pf.SERVED_GGUF_PATH.is_file()):
            pytest.skip("requires host-local witnessed reconversion evidence")
        rep = pf.run_preflight(conversion_record=rec_path)
        by = {f["id"]: f for f in rep["findings"]}
        assert by["provenance.conversion"]["status"] == "PROVEN"
        assert by["provenance.verdict"]["status"] == "complete"
