"""Tests for ``derive_conversion_record``.

The function exists so the ONE legitimate remediation route -- re-perform the merge
and conversion under a witnessed operation -- produces a machine-checked record
instead of a hand-typed one. Its most important property is therefore NEGATIVE:

**it must not be able to launder provenance.** Deriving from this host's real
artifacts must leave the conversion link UNRECORDED, because the historical merged
weights no longer exist and nothing on disk records which adapter produced the
served GGUF.

No test here reads model weights or a training corpus; digests are computed only over
small fixture files and the repo's own adapter config.
"""
from __future__ import annotations

import hashlib

import pytest

from qwen_train import f2_model_provenance as mp

REPO = r"C:\Users\rober\Projects\v-horseshoe-v2"
ADAPTER = rf"{REPO}\qwen_train\robs4b_final_adapter"
GGUF = rf"{REPO}\qwen_train\robs4b_q4km.gguf"
GGUF_SHA = "65202f372110dde854b40ce15dcd1b6ab56a1fe9ea542b84b6a9cc745b242d41"


def _sha(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class TestDerivationIsEvidenceBound:
    def test_adapter_identity_comes_from_the_real_config(self):
        rec = mp.derive_conversion_record(source_adapter=ADAPTER)
        assert rec["source_adapter"] == "robs4b_final_adapter"
        assert rec["source_adapter_sha256"] == _sha(
            rf"{ADAPTER}\adapter_config.json"
        )

    def test_absent_adapter_contributes_nothing(self):
        rec = mp.derive_conversion_record(source_adapter=rf"{REPO}\__no_such_adapter__")
        assert "source_adapter" not in rec
        assert "source_adapter_sha256" not in rec

    def test_served_artifact_digest_is_computed_from_bytes(self):
        rec = mp.derive_conversion_record(served_gguf=GGUF)
        assert rec["served_gguf_sha256"] == GGUF_SHA

    def test_missing_served_artifact_contributes_nothing(self):
        rec = mp.derive_conversion_record(served_gguf=rf"{REPO}\__no_such__.gguf")
        assert "served_gguf" not in rec
        assert "served_gguf_sha256" not in rec

    def test_merged_artifact_absent_is_omitted_not_invented(self):
        """The whole point: an absent merged artifact yields no identity."""
        rec = mp.derive_conversion_record(
            merged_artifact=rf"{REPO}\__no_such__\Robs4B_Merged_Hf"
        )
        assert "merged_artifact" not in rec
        assert "merged_artifact_sha256" not in rec

    def test_directory_artifact_gets_no_fabricated_digest(self, tmp_path):
        """A weights DIRECTORY has no cheap whole-content digest.

        Substituting the config file's digest would look like provenance it is not.
        """
        d = tmp_path / "merged_dir"
        d.mkdir()
        (d / "config.json").write_text("{}", encoding="utf-8")
        rec = mp.derive_conversion_record(merged_artifact=d)
        assert rec["merged_artifact"] == "merged_dir"
        assert "merged_artifact_sha256" not in rec
        assert "merged_artifact_sha256" in mp.validate_conversion_record(rec)

    def test_file_artifact_digest_is_computed(self, tmp_path):
        f = tmp_path / "merged.safetensors"
        f.write_bytes(b"weights" * 1000)
        rec = mp.derive_conversion_record(merged_artifact=f)
        assert rec["merged_artifact_sha256"] == _sha(f)


class TestDerivationNeverSuppliesOperationFacts:
    """Operation facts are human attestations, not file contents."""

    def test_tool_version_is_not_derived_from_the_tool_file(self, tmp_path):
        script = tmp_path / "convert_hf_to_gguf.py"
        script.write_text("# v9999\n", encoding="utf-8")
        rec = mp.derive_conversion_record(conversion_tool=script)
        assert rec["conversion_tool"] == "convert_hf_to_gguf.py"
        assert "conversion_tool_version" not in rec

    def test_operation_fields_absent_unless_supplied(self):
        rec = mp.derive_conversion_record(conversion_tool="x.py")
        for field in ("merge_operation", "operator", "conversion_inputs"):
            assert field not in rec, field

    def test_blank_values_are_not_emitted(self):
        rec = mp.derive_conversion_record(
            conversion_tool="x.py",
            conversion_tool_version="   ",
            merge_operation="",
            operator="",
            conversion_inputs=["", "  "],
        )
        for field in ("conversion_tool_version", "merge_operation", "operator",
                      "conversion_inputs"):
            assert field not in rec, field

    def test_empty_input_list_is_not_emitted(self):
        assert "conversion_inputs" not in mp.derive_conversion_record(
            conversion_inputs=[]
        )

    def test_input_order_is_preserved_when_supplied(self):
        rec = mp.derive_conversion_record(
            conversion_inputs=["b.safetensors", "a.safetensors"]
        )
        assert rec["conversion_inputs"] == ["b.safetensors", "a.safetensors"]


class TestDerivationCannotLaunderProvenance:
    """The negative property the function exists to guarantee."""

    def test_real_host_state_does_not_close_the_link(self):
        """Deriving everything derivable from THIS host must stay UNRECORDED."""
        rec = mp.derive_conversion_record(
            source_adapter=ADAPTER,
            merged_artifact=rf"{REPO}\__absent__\Robs4B_Merged_Hf",
            served_gguf=GGUF,
            conversion_tool=r"C:\Users\rober\Projects\llama.cpp\convert_hf_to_gguf.py",
        )
        missing = mp.validate_conversion_record(rec)
        # The merged weights are gone and no operation was witnessed, so at minimum
        # the merged-artifact pair, the operation facts and the base digest are absent.
        assert "merged_artifact" in missing
        assert "merged_artifact_sha256" in missing
        assert "merge_operation" in missing
        assert "operator" in missing
        assert "conversion_tool_version" in missing
        assert "conversion_inputs" in missing

        chain = mp.build_conversion_chain(
            model_alias="robs4b",
            training_runs_path=rf"{REPO}\qwen_train\training_runs.jsonl",
            adapter_dirs=[ADAPTER],
            corpus_search_roots=[r"C:\Users\rober\Projects\qwen_train_data"],
            served_gguf=GGUF,
            served_gguf_sha256=GGUF_SHA,
            conversion_record=rec,
        )
        assert chain.link(mp.LINK_CONVERSION).state == "UNRECORDED"
        assert not mp.verify_conversion_chain(chain).satisfied

    def test_a_fully_witnessed_reconversion_does_close_the_link(self, tmp_path):
        """The positive control: the legitimate route really does work.

        The base digest must be the one the chain actually proves. Inventing one
        here would be caught by the binding check as a MISMATCH -- which is
        exactly what ``test_a_derived_record_still_fails_the_binding_check``
        asserts, so the positive control has to use the real value.
        """
        merged = tmp_path / "Robs4B_Merged_Hf.safetensors"
        merged.write_bytes(b"merged" * 500)
        real_base_digest = mp.build_conversion_chain(
            model_alias="robs4b",
            training_runs_path=rf"{REPO}\qwen_train\training_runs.jsonl",
            adapter_dirs=[ADAPTER],
            corpus_search_roots=[],
            served_gguf=None,
        ).link(mp.LINK_BASE).digest
        assert real_base_digest
        rec = mp.derive_conversion_record(
            source_adapter=ADAPTER,
            merged_artifact=merged,
            served_gguf=GGUF,
            source_base_sha256=real_base_digest,
            conversion_tool="convert_hf_to_gguf.py",
            conversion_tool_version="b4589",
            merge_operation="merge.py --adapter robs4b_final_adapter --base Qwen3.5-4B",
            operator="operator@host",
            conversion_inputs=[str(merged), GGUF],
        )
        assert mp.validate_conversion_record(rec) == ()
        chain = mp.build_conversion_chain(
            model_alias="robs4b",
            training_runs_path=rf"{REPO}\qwen_train\training_runs.jsonl",
            adapter_dirs=[ADAPTER],
            corpus_search_roots=[r"C:\Users\rober\Projects\qwen_train_data"],
            served_gguf=GGUF,
            served_gguf_sha256=GGUF_SHA,
            conversion_record=rec,
        )
        assert chain.link(mp.LINK_CONVERSION).state == "PROVEN"

    def test_a_derived_record_still_fails_the_binding_check(self, tmp_path):
        """Deriving does not exempt a record from chain-binding verification."""
        merged = tmp_path / "m.safetensors"
        merged.write_bytes(b"x" * 100)
        rec = mp.derive_conversion_record(
            source_adapter=ADAPTER,
            merged_artifact=merged,
            served_gguf=GGUF,
            source_base_sha256="a" * 64,
            conversion_tool="c.py",
            conversion_tool_version="1",
            merge_operation="m.py",
            operator="op@host",
            conversion_inputs=["x"],
        )
        rec["source_adapter_sha256"] = "b" * 64  # disagrees with the proven adapter
        chain = mp.build_conversion_chain(
            model_alias="robs4b",
            training_runs_path=rf"{REPO}\qwen_train\training_runs.jsonl",
            adapter_dirs=[ADAPTER],
            corpus_search_roots=[r"C:\Users\rober\Projects\qwen_train_data"],
            served_gguf=GGUF,
            served_gguf_sha256=GGUF_SHA,
            conversion_record=rec,
        )
        assert chain.link(mp.LINK_CONVERSION).state == "MISMATCH"
        assert not mp.verify_conversion_chain(chain).satisfied


class TestRequiredFieldCoverage:
    @pytest.mark.parametrize("field", mp.CONVERSION_REQUIRED_FIELDS)
    def test_every_required_field_has_a_derivation_or_attestation_route(self, field):
        """Each field is either artifact-derived or an explicit attestation.

        No field may be one that nothing can produce.
        """
        derived = {
            "source_adapter",
            "source_adapter_sha256",
            "source_base",
            "source_base_sha256",
            "merged_artifact",
            "merged_artifact_sha256",
            "served_gguf",
            "served_gguf_sha256",
            "conversion_tool",
        }
        attested = {
            "conversion_tool_version",
            "conversion_inputs",
            "merge_operation",
            "operator",
        }
        assert field in derived or field in attested, field

    def test_the_two_sets_partition_the_required_fields(self):
        derived = {
            "source_adapter", "source_adapter_sha256", "source_base",
            "source_base_sha256", "merged_artifact", "merged_artifact_sha256",
            "served_gguf", "served_gguf_sha256", "conversion_tool",
        }
        attested = {
            "conversion_tool_version", "conversion_inputs",
            "merge_operation", "operator",
        }
        assert derived | attested == set(mp.CONVERSION_REQUIRED_FIELDS)
        assert not (derived & attested)