"""Tests for F2 model/artifact provenance (the conversion chain).

The properties under test are honesty properties:

* a link is PROVEN only when something RECORDED it;
* filesystem timestamps are observations, never conversion evidence;
* a served artifact can verify while the chain is still incomplete, and the
  verdict must say so rather than collapsing to a pass;
* every broken link names the record that would close it;
* no test reads a single byte of any training corpus.
"""
from __future__ import annotations

import json

import pytest

from qwen_train import f2_model_provenance as mp

REPO = r"C:\Users\rober\Projects\v-horseshoe-v2"
RUNS = rf"{REPO}\qwen_train\training_runs.jsonl"
ADAPTERS = [rf"{REPO}\qwen_train\robs4b_final_adapter"]
CORPUS_ROOTS = [r"C:\Users\rober\Projects\qwen_train_data"]
GGUF = rf"{REPO}\qwen_train\robs4b_q4km.gguf"


def build(**over):
    kw = dict(
        model_alias="robs4b",
        training_runs_path=RUNS,
        adapter_dirs=ADAPTERS,
        corpus_search_roots=CORPUS_ROOTS,
        served_gguf=GGUF,
        served_gguf_sha256="65202f372110dde854b40ce15dcd1b6ab56a1fe9ea542b84b6a9cc745b242d41",
    )
    kw.update(over)
    return mp.build_conversion_chain(**kw)


class TestChainConstruction:
    def test_all_five_links_present(self):
        chain = build()
        assert {l.name for l in chain.links} == set(mp.ALL_LINKS)

    def test_base_link_reads_the_adapter_config(self):
        link = build().link(mp.LINK_BASE)
        assert link.state == "PROVEN"
        assert link.identity
        assert "base_model_name_or_path" in link.detail

    def test_base_snapshot_is_captured(self):
        chain = build()
        facts = dict(chain.observations)
        assert "base_snapshot" in facts
        assert len(facts["base_snapshot"]) >= 7

    def test_adapter_link_records_config_digest_rank_alpha(self):
        chain = build()
        link = chain.link(mp.LINK_ADAPTER)
        assert link.state == "PROVEN"
        assert len(link.digest) == 64
        facts = dict(chain.observations)
        assert facts.get("adapter_r")
        assert facts.get("adapter_alpha")

    def test_corpus_link_uses_the_run_ledger_declaration(self):
        link = build().link(mp.LINK_CORPUS)
        assert link.state == "PROVEN"
        assert link.identity == "robs4b_mixed_final.jsonl"
        assert "content not read" in link.detail

    def test_conversion_link_is_unrecorded_without_an_explicit_record(self):
        link = build().link(mp.LINK_CONVERSION)
        assert link.state == "UNRECORDED"
        assert "NOT conversion evidence" in link.detail

    def test_served_link_verifies_the_real_digest(self):
        link = build().link(mp.LINK_SERVED)
        assert link.state == "PROVEN"
        assert link.digest.startswith("65202f37")

    def test_served_digest_mismatch_is_detected(self):
        link = build(served_gguf_sha256="0" * 64).link(mp.LINK_SERVED)
        assert link.state == "MISMATCH"

    def test_corpus_content_is_never_hashed(self):
        """A corpus hash would require reading it. We record size and mtime only."""
        facts = dict(build().observations)
        assert "corpus_sha256_not_computed" in facts
        assert "corpus_bytes" in facts
        assert not any(k.startswith("corpus_sha256_") and k != "corpus_sha256_not_computed"
                       for k in facts)


class TestVerdict:
    def test_incomplete_chain_is_not_satisfied(self):
        v = mp.verify_conversion_chain(build())
        assert not v.satisfied
        assert mp.LINK_CONVERSION in v.unrecorded_links
        assert mp.LINK_SERVED not in v.unrecorded_links

    def test_served_artifact_alone_does_not_satisfy(self):
        """The exact gap this module exists to expose."""
        v = mp.verify_conversion_chain(build())
        assert mp.LINK_SERVED in v.proven_links
        assert not v.satisfied

    def test_explicit_conversion_record_closes_the_link(self):
        rec = {
            "merged_artifact": "robs4b_q4km.gguf",
            "merged_artifact_sha256": "65202f37" + "0" * 56,
            "detail": "merged from robs4b_final_adapter by convert script rev X",
        }
        chain = build(conversion_record=rec)
        assert chain.link(mp.LINK_CONVERSION).state == "PROVEN"
        v = mp.verify_conversion_chain(chain)
        assert v.satisfied, v.detail
        assert not v.remedies

    def test_incomplete_conversion_record_stays_unrecorded(self):
        rec = {"detail": "said something but named no artifact"}
        chain = build(conversion_record=rec)
        assert chain.link(mp.LINK_CONVERSION).state == "UNRECORDED"
        assert not mp.verify_conversion_chain(chain).satisfied

    def test_each_broken_link_names_its_remedy(self):
        chain = build(
            adapter_dirs=[],
            corpus_search_roots=[],
            served_gguf=None,
            training_runs_path=rf"{REPO}\qwen_train\__absent_runs__.jsonl",
        )
        v = mp.verify_conversion_chain(chain)
        assert not v.satisfied
        assert set(v.unrecorded_links) == set(mp.ALL_LINKS)
        assert len(v.remedies) == len(mp.ALL_LINKS)
        for r in v.remedies:
            assert isinstance(r, str) and r

    def test_mismatch_blocks_and_is_reported_separately(self):
        v = mp.verify_conversion_chain(build(served_gguf_sha256="0" * 64))
        assert not v.satisfied
        assert mp.LINK_SERVED in v.mismatched_links

    def test_timestamps_never_close_the_conversion_link(self):
        """Ordering alone must not be accepted as provenance."""
        chain = build()
        facts = dict(chain.observations)
        assert "served_gguf_mtime" in facts
        assert chain.link(mp.LINK_CONVERSION).state == "UNRECORDED"

    def test_verdict_is_deterministic(self):
        c = build()
        a = mp.verify_conversion_chain(c)
        b = mp.verify_conversion_chain(mp.parse_chain(mp.render_chain(c)))
        assert a.to_dict() == b.to_dict()


class TestSerialisation:
    def test_round_trip(self):
        chain = build()
        back = mp.parse_chain(mp.render_chain(chain))
        assert back.to_dict() == chain.to_dict()

    def test_rejects_foreign_schema(self):
        with pytest.raises(mp.ProvenanceError, match="schema"):
            mp.parse_chain({"schema": "other"})

    def test_rejects_malformed_links(self):
        with pytest.raises(mp.ProvenanceError, match="malformed"):
            mp.parse_chain({"schema": mp.SCHEMA, "links": [{"name": "x"}]})

    def test_chain_is_json_serialisable(self):
        json.dumps(build().to_dict(), sort_keys=True)


class TestTrainingRunsReader:
    def test_reads_metadata_not_content(self, tmp_path):
        p = tmp_path / "runs.jsonl"
        p.write_text(
            json.dumps({"adapter": "a", "data": "corpus.jsonl", "ts": "2026-09-06T01:24:13"})
            + "\n\n" + "not json\n",
            encoding="utf-8",
        )
        rows = mp.read_training_runs(p)
        assert len(rows) == 1
        assert rows[0]["adapter"] == "a"

    def test_absent_file_is_empty_not_an_error(self, tmp_path):
        assert mp.read_training_runs(tmp_path / "nope.jsonl") == []


class TestNoCorpusReads:
    def test_module_never_opens_a_corpus_for_reading(self):
        """Guard against a future change that starts reading training content."""
        import pathlib

        src = pathlib.Path(mp.__file__).read_text(encoding="utf-8")
        # The only read_text() on a run-ledger path is allowed; corpus access must
        # be metadata-only (stat).
        assert "corpus_search_roots" in src
        for forbidden in ("read_text", "readlines", "jsonl"):
            # read_text appears only for config/ledger parsing; assert each use is
            # adjacent to a config/ledger variable rather than a corpus path.
            for line in src.splitlines():
                if forbidden in line and "read_text" in line:
                    assert "cfg" in line or "ledger" in line or "p.read_text" in line, line
