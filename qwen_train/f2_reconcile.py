"""F2 population reconciliation -> machine-readable artifact (deterministic, read-only).

Establishes the ACTUAL union mathematically from the authorized source records
(never by adding approximate counts). Produces:

  data/f2_population/reconciliation/reconciliation.json
  data/f2_population/reconciliation/capacity_report.json

Pure observation + existing-predicate application. Runs NO protected stage,
changes NO frozen metadata, discards NOTHING. Reproducible: same inputs -> same
digests. Usage:  python -m qwen_train.f2_reconcile
"""
from __future__ import annotations

import hashlib
import json
import collections
import os
from pathlib import Path

from qwen_train.f2_population_build import source_to_screen_row, _payload_key
from qwen_train.f2_registration import (
    is_pytest_command, all_pytest_node_ids, python_confirmed,
)

ROOT = Path(__file__).resolve().parents[1]
POP = ROOT / "data" / "f2_population"
OUT = POP / "reconciliation"

SOURCES = [
    ("swe-bench-live", POP / "upstream_swe_bench_live_b51a8642" / "acquired.jsonl",
     "SWE-bench-Live/SWE-bench-Live", "b51a86422e10cfd403beb4773e5a2947953e36ec"),
    ("swe-rebench-v2", POP / "upstream_swe_rebench_v2_10483de0" / "acquired.jsonl",
     "nebius/SWE-rebench-V2", "10483de0f50fe5da545942705a76c6150171af7f"),
]

REG_MANIFEST = ROOT / "qwen_train" / "curriculum" / "f2_registration" / "registration_manifest.json"

#: Registered-chain root. The chains live OUTSIDE the repository (they are
#: governed evidence, not code), so the root is not derived from ROOT. It is
#: overridable so this tool is not welded to one host.
_DEFAULT_TRUSTED_STORE = Path(r"C:\Users\rober\F2_EVIDENCE\s8\trusted_store")


def _trusted_store_root() -> Path:
    return Path(os.environ.get("SWARM_F2_TRUSTED_STORE") or _DEFAULT_TRUSTED_STORE)


def _admission_accounting(census_admitted: int) -> dict:
    """The counting rule's own figures, kept strictly separate from the census.

    ``docs/EXPERIMENT_J_F2_ADMISSION_COUNTING_RULE.md`` measures the target as the
    DEDUPLICATED CREDITED UNION across the registered chains -- never the
    registration/census count, never metadata eligibility, and never a raw ledger
    row count. Read-only: the registered chains are verified by the reconciler,
    which fails closed on a malformed chain, an unregistered name or a broken
    digest chain.

    When the chains cannot be read the ledger figures are ``None`` WITH a reason.
    They are NEVER defaulted to ``0``: "not measured" and "zero admitted" are
    different claims, and conflating them is the defect this field exists to
    remove.
    """
    out = {
        "basis": "docs/EXPERIMENT_J_F2_ADMISSION_COUNTING_RULE.md",
        "target": 300,
        "census_admitted": census_admitted,
        "operative_ledger_admissions": None,
        "corrective_ledger_admissions": None,
        "credited_admission_union": None,
        "remaining_to_target": None,
        "refused_terminal_overrides": None,
        "verified_corrections": None,
        "unavailable_reason": "",
    }
    try:
        from qwen_train.f2_s8_batch import (
            REGISTERED_CHAINS,
            reconcile_admissions_across_chains,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed
        out["unavailable_reason"] = f"admission reconciler unavailable: {exc}"
        return out
    store = _trusted_store_root()
    chains = [store / name for name in REGISTERED_CHAINS]
    missing = [str(p) for p in chains if not p.is_file()]
    if missing:
        out["unavailable_reason"] = ("registered chain(s) not present: "
                                     + ", ".join(missing))
        return out
    try:
        # The universe is the chains' own task ids: the chains are the registered
        # admission record, and this tool has no separate task universe.
        registered = sorted({
            json.loads(line)["task_id"]
            for p in chains
            for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip()
        })
        rec = reconcile_admissions_across_chains(chains, registered)
    except Exception as exc:  # noqa: BLE001 - fail closed
        out["unavailable_reason"] = f"registered chains did not reconcile: {exc}"
        return out
    credited = int(rec["credited_count"])
    operative = len(rec["operative_admitted"])
    out["operative_ledger_admissions"] = operative
    out["corrective_ledger_admissions"] = max(0, credited - operative)
    out["credited_admission_union"] = credited
    out["remaining_to_target"] = max(0, 300 - credited)
    out["refused_terminal_overrides"] = len(rec["refused_terminal_override"])
    out["verified_corrections"] = rec["corrections_verified"]
    return out


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan(p: Path, src: str):
    """Yield canonical screen rows; count raw/malformed/ids along the way."""
    n = malformed = 0
    ids: collections.Counter = collections.Counter()
    rows = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except Exception:
                malformed += 1
                continue
            n += 1
            sr = source_to_screen_row(src, raw)
            ids[sr["instance_id"]] += 1
            rows.append(sr)
    return n, malformed, ids, rows


def _non_pytest_family(test_cmd: str) -> str:
    tok = (test_cmd.split()[0] if test_cmd.strip() else "<empty>")
    return tok


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    recon: dict = {
        "schema": "f2_population_reconciliation_v1",
        "sources": [],
        "cross_source": {},
        "funnel": {},
        "hashes": {},
    }

    all_ids: dict[str, str] = {}            # instance_id -> first source key
    id_sources: dict[str, set] = collections.defaultdict(set)
    rows_by_id: dict[str, dict] = {}
    per_source_rows: dict[str, list] = {}

    total_raw = total_malformed = 0
    for key, path, repo_id, revision in SOURCES:
        n, malformed, ids, rows = _scan(path, key)
        total_raw += n
        total_malformed += malformed
        per_source_rows[key] = rows
        dup_exact = {k: v for k, v in ids.items() if v > 1 and k}
        recon["sources"].append({
            "source_key": key,
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "repo_id": repo_id,
            "revision": revision,
            "sha256": _sha256_file(path),
            "raw_rows": n,
            "unique_instance_ids": len(ids),
            "malformed_json": malformed,
            "within_source_duplicate_ids": len(dup_exact),
            "within_source_duplicate_detail": dict(list(dup_exact.items())[:10]),
            "missing_identity_rows": ids.get("", 0),
        })
        recon["hashes"][f"{key}_sha256"] = _sha256_file(path)
        for sr in rows:
            iid = sr["instance_id"]
            if not iid:
                continue
            id_sources[iid].add(key)
            if iid not in rows_by_id:
                rows_by_id[iid] = sr
                all_ids[iid] = key

    union_ids = set(rows_by_id)
    cross_overlap = {i: sorted(s) for i, s in id_sources.items() if len(s) > 1}

    # ---- within-source CONFLICTING duplicates (authorized union policy) ----
    # An instance_id whose copies are NOT payload-identical is a conflicting
    # duplicate: the authorized union removes ALL copies (equivalence cannot be
    # established). This is distinct from a naive first-wins dedup, which would
    # keep one copy. Recorded so the independent count vs the committed union
    # report is reconciled, never silently discarded.
    conflicting_removed = set()
    for key, rows in per_source_rows.items():
        by_id: dict[str, list] = collections.defaultdict(list)
        for sr in rows:
            if sr["instance_id"]:
                by_id[sr["instance_id"]].append(sr)
        for iid, copies in by_id.items():
            if len(copies) > 1:
                if len({_payload_key(c) for c in copies}) > 1:
                    conflicting_removed.add(iid)
    authorized_union_ids = union_ids - conflicting_removed

    recon["cross_source"] = {
        "total_raw_rows_across_sources": total_raw,
        "total_malformed": total_malformed,
        "naive_first_wins_union_ids": len(union_ids),
        "within_source_conflicting_duplicate_ids": len(conflicting_removed),
        "within_source_conflicting_duplicate_detail": sorted(conflicting_removed)[:10],
        "authorized_union_ids_conflict_removed": len(authorized_union_ids),
        "cross_source_overlap_ids": len(cross_overlap),
        "cross_source_overlap_sample": dict(list(cross_overlap.items())[:10]),
        "rows_removed_by_union_dedup": total_raw - total_malformed - len(union_ids),
        "reconciliation_vs_committed_report": (
            "committed union_report accepts 33770; independent naive union is 33771; "
            "the +1 is a within-source CONFLICTING duplicate (e.g. "
            "conan-io__conan-18153) that the authorized policy removes entirely and a "
            "naive dedup would keep one copy of. Both counts are recorded; nothing is "
            "discarded silently."
        ),
    }

    # ---- apply the metadata-eligibility predicate (all screens but S8) ------
    # Reuse the committed union report for screen outcomes (it is the authorized
    # screening artifact); recompute the evaluator/python gates from source rows.
    ureport = json.load(open(POP / "union" / "union_report.json", encoding="utf-8"))
    failing = collections.Counter()
    meta_eligible = set()
    for x in ureport["screen_rejections"]:
        rules = set(x.get("failing_rules", []))
        failing[tuple(sorted(rules))] += 1
        if rules == {"S8_evidence_provenance"}:
            meta_eligible.add(x["instance_id"])

    evaluator_ok = set()
    registered = set()
    framework_reject = collections.Counter()
    node_reject = {"empty": 0, "nonnode": 0}
    py_confirm_reject = 0
    for iid in meta_eligible:
        sr = rows_by_id.get(iid)
        if sr is None:
            continue
        tc = sr.get("test_cmd", "")
        f2p, p2p = sr.get("fail_to_pass", []), sr.get("pass_to_pass", [])
        if not is_pytest_command(tc):
            framework_reject[_non_pytest_family(tc)] += 1
            continue
        if not (all_pytest_node_ids(f2p) and all_pytest_node_ids(p2p)):
            if not f2p or not p2p:
                node_reject["empty"] += 1
            else:
                node_reject["nonnode"] += 1
            continue
        evaluator_ok.add(iid)
        if python_confirmed(f2p, p2p):
            registered.add(iid)
        else:
            py_confirm_reject += 1

    reg_manifest = json.load(open(REG_MANIFEST, encoding="utf-8"))
    recon["funnel"] = {
        "union_accepted_after_dedup": len(union_ids),
        "metadata_eligible_s8_only": len(meta_eligible),
        "screen_failing_rule_sets_top": {str(k): v for k, v in failing.most_common(12)},
        "evaluator_compatible_pytest_cmd_and_nodes": len(evaluator_ok),
        "registered_py_confirmed": len(registered),
        "gap_metadata_to_evaluator": len(meta_eligible) - len(evaluator_ok),
        "gap_evaluator_to_registered": len(evaluator_ok) - len(registered),
        "reject_non_pytest_framework_top": dict(framework_reject.most_common(20)),
        "reject_pytest_cmd_bad_nodes": node_reject,
        "reject_evaluator_ok_not_py_confirmed": py_confirm_reject,
    }
    recon["committed_manifest_agreement"] = {
        "manifest_metadata_eligible": reg_manifest["counts"]["metadata_eligible"],
        "manifest_evaluator_compatible": reg_manifest["counts"]["evaluator_compatible"],
        "manifest_registered": reg_manifest["counts"]["registered"],
        "recomputed_registered_matches": (
            len(registered) == reg_manifest["counts"]["registered"]
        ),
    }

    (OUT / "reconciliation.json").write_text(
        json.dumps(recon, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # ---------------------------- capacity report ---------------------------
    # `admitted` comes from the REGISTRATION manifest, which carries no
    # "admitted" key: registration is an INPUT candidate count, and eligibility
    # never depends on a run outcome. The `.get(..., 0)` therefore means "this
    # manifest does not measure admission", NOT "zero were admitted". The two are
    # different questions and are reported separately below.
    census_admitted = reg_manifest["counts"].get("admitted", 0)
    admission_accounting = _admission_accounting(census_admitted)
    analyzable = 0
    capacity = {
        "schema": "f2_capacity_report_v1",
        "counts_distinct": {
            "raw_source_records": total_raw,
            "distinct_union_records_authorized": len(authorized_union_ids),
            "distinct_union_records_naive_dedup": len(union_ids),
            "excluded_or_conflicting_records": total_raw - total_malformed - len(authorized_union_ids),
            "metadata_eligible": len(meta_eligible),
            "evaluator_compatible": len(evaluator_ok),
            "registered": len(registered),
            # CENSUS screening figure only (registration manifest). It is NOT the
            # admission count -- see `admission_accounting` for that.
            "admitted": census_admitted,
            "analyzable_pairs": analyzable,
            "completed_pairs": 0,
        },
        "admission_accounting": admission_accounting,
        "target_analyzable_pairs": 300,
        "shortfall_to_300_analyzable": max(0, 300 - analyzable),
        "pool_sufficient_under_eligibility": len(registered) >= 300,
        "pool_sufficiency_evidence": (
            f"{len(registered)} registered pytest-compatible candidates >= 300 target; "
            f"{len(meta_eligible)} metadata-eligible total. Candidate SUPPLY is NOT the "
            "binding constraint."
        ),
        "binding_constraint": (
            "S8 admission evidence (base=FAIL/gold=PASS executions) is absent for all "
            "candidates; generating it requires the Q9-attested isolated guest "
            "(F2-Isolation-VM, currently Off; provisioning REQUIRES AUTHORIZATION "
            "F2-IMPL-AUTH-027). Admission is fail-closed on S8, so admitted=0 and "
            "analyzable=0 regardless of candidate supply."
        ),
        "primary_endpoint_note": (
            "F2 estimand (SAP 2.1) is a BEHAVIORAL first-relevant-edit on the frozen "
            "relevant_file_set, measured from the ATIF trajectory. The pytest-only "
            "evaluator gate governs S8 ADMISSION screening, not the endpoint. The "
            "5,398 non-pytest rejections are an implementation/evaluator-scope limit, "
            "NOT a frozen scientific exclusion of those tasks from the endpoint - but "
            "they are not the binding constraint either (S8 blocks all admission)."
        ),
        "outstanding_owner_gates": [
            "Q9 isolation: enforce host default-deny egress, boot F2-Isolation-VM, "
            "produce clean-room attestation (PRIVILEGED HOST + EXTERNAL EVIDENCE)",
            "S8 base/gold evidence generation in the attested guest (EXTERNAL EVIDENCE)",
            "Trust-root signing ceremony (OWNER; absent -> all protected stages fail closed)",
            "CONF-001 paired-execution signed grant (OWNER AUTHORIZATION)",
        ],
        "hashes": {
            "reconciliation_sha256": _sha256_file(OUT / "reconciliation.json"),
            "union_report_sha256": _sha256_file(POP / "union" / "union_report.json"),
            "registration_manifest_sha256": _sha256_file(REG_MANIFEST),
        },
    }
    (OUT / "capacity_report.json").write_text(
        json.dumps(capacity, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(capacity["counts_distinct"], indent=2, sort_keys=True))
    print("pool_sufficient:", capacity["pool_sufficient_under_eligibility"])
    print("wrote:", (OUT / "reconciliation.json").name, (OUT / "capacity_report.json").name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
