"""``f2-s8-state-set/v1`` run-level aggregate — tests.

Covers: known-answer determinism, ordering invariance, entry/field mutation,
duplicate/missing/unexpected records, manifest mismatch, side substitution,
non-ES_VERIFIED pairs, malformed/non-canonical input, environment-label
binding, evaluator-digest uniformity, and integration with the REAL 18-task
derivation artifact when present (skipped otherwise).

No network, no service, no VM, no authorization consumption.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwen_train import f2_execution_state as es
from qwen_train import f2_s8_state_set as ss

VALID_64 = "a" * 64
OTHER_64 = "b" * 64
MANIFEST_DIGEST = "c" * 64


# --------------------------------------------------------------------------
# helpers — a synthetic derivation document with real verified records
# --------------------------------------------------------------------------
def _record(tmp_path: Path, task_id: str, side: str, *, uniq: str) -> dict:
    root = tmp_path / f"tree-{uniq}-{task_id}-{side}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text(f"# {uniq} {task_id} {side}\n", encoding="utf-8")
    rec = es.build_execution_state_from_disk(
        str(root),
        instance_id=task_id,
        repository="octocat/hello",
        base_commit="c" * 40,
        execution_state_identity=side,
        environment_identity="campaign/py3.14.8/cpython-3.14.8",
        test_command="python -m pytest -q",
        evaluator_implementation_digest=VALID_64,
        procedure_id="f2-s8-evaluator/v1",
        protocol_version="1",
        test_patch_digest=OTHER_64,
        run_nonce=f"stage1-baseline-{task_id}-{side}",
        gold_patch_digest=(OTHER_64 if side == "gold" else ""),
        measured_at="2026-10-17T00:00:00Z",
        interpreter="cpython-3.14.8:host/.venv/Scripts/python.exe",
        distributions=(VALID_64, 5),
    )
    return rec.to_dict()


def _derivation(tmp_path: Path, task_ids=("acme__repo-1", "acme__repo-2")) -> dict:
    results = []
    for i, task_id in enumerate(task_ids):
        base = _record(tmp_path, task_id, "base", uniq=f"u{i}b")
        gold = _record(tmp_path, task_id, "gold", uniq=f"u{i}g")
        results.append(
            {
                "task_id": task_id,
                "base": base,
                "gold": gold,
                "pair_state": "ES_VERIFIED",
                "base_state_digest": base["measured_state_digest"],
                "gold_state_digest": gold["measured_state_digest"],
            }
        )
    return {
        "schema_version": "f2-s8-state-derivation/v1",
        "candidate_manifest_digest": MANIFEST_DIGEST,
        "environment_label": "campaign",
        "results": results,
    }


def _aggregate(tmp_path: Path, *, side: str = "base", **kw) -> ss.StateSetAggregate:
    doc = kw.pop("doc", None) or _derivation(tmp_path)
    return ss.build_state_set_aggregate(
        derivation=doc,
        candidate_manifest_digest=kw.pop("candidate_manifest_digest", MANIFEST_DIGEST),
        side=side,
        **kw,
    )


# --------------------------------------------------------------------------
# known-answer + determinism
# --------------------------------------------------------------------------
def test_known_answer_digest_is_stable_across_rebuilds(tmp_path: Path) -> None:
    first = _aggregate(tmp_path)
    second = _aggregate(tmp_path)
    assert first.aggregate_digest == second.aggregate_digest
    assert len(first.aggregate_digest) == 64
    assert first.compute_digest() == first.aggregate_digest


def test_payload_carries_versioned_type_identifier(tmp_path: Path) -> None:
    agg = _aggregate(tmp_path)
    payload = agg.payload()
    assert payload["schema_version"] == "f2-s8-state-set/v1"
    assert payload["type"] == "urn:swarm-os:f2-s8:execution-state-set:1"
    assert payload["side"] == "base"
    assert payload["candidate_manifest_digest"] == MANIFEST_DIGEST
    assert payload["environment_identity"] == "campaign"
    assert payload["task_count"] == 2


# --------------------------------------------------------------------------
# ordering invariance
# --------------------------------------------------------------------------
def test_result_order_does_not_change_the_aggregate(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    forward = ss.build_state_set_aggregate(
        derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
    )
    doc_rev = dict(doc)
    doc_rev["results"] = list(reversed(doc["results"]))
    reverse = ss.build_state_set_aggregate(
        derivation=doc_rev, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
    )
    assert forward.aggregate_digest == reverse.aggregate_digest
    ids = [e[0] for e in forward.entries]
    assert ids == sorted(ids)


# --------------------------------------------------------------------------
# mutation / binding
# --------------------------------------------------------------------------
def test_swapping_records_between_tasks_changes_the_aggregate(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    base = ss.build_state_set_aggregate(
        derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
    )
    # Swap the two tasks' base records (same record SET, different task binding).
    mutated = json.loads(json.dumps(doc))
    r = mutated["results"]
    r[0]["base"], r[1]["base"] = r[1]["base"], r[0]["base"]
    # instance_id inside each record still names its true task, so the aggregate
    # must FAIL (record verification against task_id), not silently re-bind.
    with pytest.raises(ss.StateSetError, match="SS_RECORD_INVALID|instance_id"):
        ss.build_state_set_aggregate(
            derivation=mutated, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
        )
    assert base.task_count == 2


def test_side_substitution_changes_the_aggregate(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    base = ss.build_state_set_aggregate(
        derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
    )
    gold = ss.build_state_set_aggregate(
        derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="gold"
    )
    assert base.aggregate_digest != gold.aggregate_digest
    assert {e[1] for e in base.entries} == {"base"}
    assert {e[1] for e in gold.entries} == {"gold"}


def test_field_mutation_invalidates_the_bound_digest(tmp_path: Path) -> None:
    agg = _aggregate(tmp_path)
    d = agg.to_dict()
    d["entries"][0]["measured_state_digest"] = OTHER_64
    verdict = ss.verify_state_set_aggregate(d, expected_side="base")
    assert not verdict.ok
    assert verdict.state == ss.SS_AGGREGATE_MISMATCH


def test_manifest_mismatch_refuses(tmp_path: Path) -> None:
    with pytest.raises(ss.StateSetError, match="SS_MANIFEST_MISMATCH"):
        ss.build_state_set_aggregate(
            derivation=_derivation(tmp_path),
            candidate_manifest_digest="d" * 64,
            side="base",
        )


def test_verify_binds_expected_manifest_and_task_set(tmp_path: Path) -> None:
    agg = _aggregate(tmp_path)
    ok = ss.verify_state_set_aggregate(
        agg.to_dict(),
        expected_side="base",
        expected_candidate_manifest_digest=MANIFEST_DIGEST,
        expected_task_ids=["acme__repo-1", "acme__repo-2"],
    )
    assert ok.ok

    wrong_manifest = ss.verify_state_set_aggregate(
        agg.to_dict(), expected_candidate_manifest_digest="d" * 64
    )
    assert wrong_manifest.state == ss.SS_MANIFEST_MISMATCH

    missing = ss.verify_state_set_aggregate(
        agg.to_dict(), expected_task_ids=["acme__repo-1"]
    )
    assert missing.state == ss.SS_TASK_SET_MISMATCH

    extra = ss.verify_state_set_aggregate(
        agg.to_dict(),
        expected_task_ids=["acme__repo-1", "acme__repo-2", "acme__repo-3"],
    )
    assert extra.state == ss.SS_TASK_SET_MISMATCH


# --------------------------------------------------------------------------
# fail-closed inputs
# --------------------------------------------------------------------------
def test_duplicate_task_entry_refuses(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    doc["results"].append(dict(doc["results"][0]))
    with pytest.raises(ss.StateSetError, match="SS_DUPLICATE_RECORD"):
        ss.build_state_set_aggregate(
            derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
        )


def test_missing_side_record_refuses(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    del doc["results"][0]["gold"]
    with pytest.raises(ss.StateSetError, match="SS_RECORD_INVALID"):
        ss.build_state_set_aggregate(
            derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="gold"
        )


def test_non_verified_pair_refuses(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    doc["results"][0]["pair_state"] = "ES_STATES_EQUAL"
    with pytest.raises(ss.StateSetError, match="ES_STATES_EQUAL"):
        ss.build_state_set_aggregate(
            derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
        )


def test_wrong_schema_refuses(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    doc["schema_version"] = "f2-s8-state-derivation/v99"
    with pytest.raises(ss.StateSetError, match="unknown derivation schema"):
        ss.build_state_set_aggregate(
            derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
        )


def test_empty_results_and_unknown_side_refuse(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    doc["results"] = []
    with pytest.raises(ss.StateSetError, match="no results"):
        ss.build_state_set_aggregate(
            derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
        )
    with pytest.raises(ss.StateSetError, match="unknown side"):
        ss.build_state_set_aggregate(
            derivation=_derivation(tmp_path),
            candidate_manifest_digest=MANIFEST_DIGEST,
            side="sideways",
        )


def test_non_canonical_input_refuses_verification(tmp_path: Path) -> None:
    agg = _aggregate(tmp_path)
    d = agg.to_dict()
    # Mutate the LAST entry so the ascending-order check still passes ("e" <
    # "é" in code-unit order); the payload now leaves the RFC-8785-equivalent
    # ASCII subset and verification must refuse with a NAMED verdict (never
    # raise).
    d["entries"][-1]["task_id"] = "acme__répo-9"  # non-ASCII
    verdict = ss.verify_state_set_aggregate(d)
    assert verdict.state == ss.SS_NONCANONICAL_INPUT


def test_verify_detects_reordered_entries(tmp_path: Path) -> None:
    agg = _aggregate(tmp_path)
    d = agg.to_dict()
    d["entries"] = list(reversed(d["entries"]))
    verdict = ss.verify_state_set_aggregate(d)
    assert not verdict.ok


def test_verify_detects_unbound_digest(tmp_path: Path) -> None:
    agg = _aggregate(tmp_path)
    d = agg.to_dict()
    d["aggregate_digest"] = ""
    verdict = ss.verify_state_set_aggregate(d)
    assert verdict.state == ss.SS_INPUT_MALFORMED


def test_verify_none_and_wrong_types(tmp_path: Path) -> None:
    assert ss.verify_state_set_aggregate(None).state == ss.SS_INPUT_MISSING
    assert ss.verify_state_set_aggregate("x").state == ss.SS_INPUT_MALFORMED


# --------------------------------------------------------------------------
# environment / evaluator binding
# --------------------------------------------------------------------------
def test_environment_label_is_required_and_bound(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    doc["environment_label"] = ""
    with pytest.raises(ss.StateSetError, match="environment_label"):
        ss.build_state_set_aggregate(
            derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
        )


def test_evaluator_digest_mismatch_refuses(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    # The evaluator digest is part of measured_state_payload, so mutating it
    # invalidates BOTH digests; rebind them in order (measured first, then the
    # record digest which covers it) so the record is internally consistent and
    # only the CROSS-record uniformity rule can catch it.
    rec = doc["results"][1]["base"]
    rec["evaluator_implementation_digest"] = OTHER_64
    rebuilt = es.ExecutionState.from_dict(rec)
    fixed = {**rec, "measured_state_digest": rebuilt.compute_measured_state_digest()}
    rebuilt2 = es.ExecutionState.from_dict(fixed)
    fixed["execution_state_digest"] = rebuilt2.compute_digest()
    doc["results"][1]["base"] = fixed
    with pytest.raises(ss.StateSetError, match="SS_EVALUATOR_MISMATCH"):
        ss.build_state_set_aggregate(
            derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="base"
        )


def test_heterogeneous_environments_are_enumerated_not_merged(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    # Rewrite one gold record under a different interpreter identity by rebuilding.
    other = _record(tmp_path, "acme__repo-2", "gold", uniq="hetero")
    other["environment_identity"] = "campaign/py3.10.11/cpython-3.10.11"
    rebuilt = es.ExecutionState.from_dict(other)
    other["execution_state_digest"] = rebuilt.compute_digest()
    doc["results"][1]["gold"] = other
    agg = ss.build_state_set_aggregate(
        derivation=doc, candidate_manifest_digest=MANIFEST_DIGEST, side="gold"
    )
    assert agg.environment_identity == "campaign"
    assert agg.environment_identities == (
        "campaign/py3.10.11/cpython-3.10.11",
        "campaign/py3.14.8/cpython-3.14.8",
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_cli_writes_both_side_aggregates(tmp_path: Path) -> None:
    doc = _derivation(tmp_path)
    src = tmp_path / "derivation.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "aggregates.json"
    rc = ss.main(["--derivation", str(src), "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["base"]["aggregate_digest"] != payload["gold"]["aggregate_digest"]
    for side in ("base", "gold"):
        verdict = ss.verify_state_set_aggregate(payload[side], expected_side=side)
        assert verdict.ok


# --------------------------------------------------------------------------
# integration with the REAL derivation artifact (when present on this host)
# --------------------------------------------------------------------------
REAL_DERIVATION = Path(
    r"C:\Users\rober\F2_EVIDENCE\s8\stage1_phase2_execution_state"
    r"\execution_states_host_baseline.json"
)


@pytest.mark.skipif(not REAL_DERIVATION.is_file(), reason="host artifact absent")
def test_real_18_task_derivation_aggregates_and_verifies() -> None:
    doc = json.loads(REAL_DERIVATION.read_text(encoding="utf-8"))
    manifest_digest = doc["candidate_manifest_digest"]
    built = 0
    for side in ("base", "gold"):
        agg = ss.build_state_set_aggregate(
            derivation=doc, candidate_manifest_digest=manifest_digest, side=side
        )
        assert agg.task_count == len(doc["results"]) >= 18
        verdict = ss.verify_state_set_aggregate(
            agg.to_dict(),
            expected_side=side,
            expected_candidate_manifest_digest=manifest_digest,
        )
        assert verdict.ok, verdict.detail
        built = agg.task_count
    base = ss.build_state_set_aggregate(
        derivation=doc, candidate_manifest_digest=manifest_digest, side="base"
    )
    gold = ss.build_state_set_aggregate(
        derivation=doc, candidate_manifest_digest=manifest_digest, side="gold"
    )
    assert base.aggregate_digest != gold.aggregate_digest
    assert built >= 18
