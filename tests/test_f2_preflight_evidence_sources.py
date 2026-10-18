"""Regression tests for the D1/D2/D3 corrections (2026-10-16).

Covers the three defects the independent reviews raised, verified against the
actual checkout rather than accepted as stated:

* **D1** -- ``f2_preflight._evidence_findings`` returned hard-coded literals, so
  preflight could never report a satisfied external gate even when genuine
  evidence existed. It now evaluates SUPPLIED sources through the authoritative
  verifier and reconciler, and still fails closed when they are absent or bad.
* **D2** -- ``f2_reconcile``'s capacity report conflated the census count with the
  counting rule's credited union, and hard-coded the shortfall.
* **D3** -- ``f2_s8_compose._refs_ok`` pinned one artifact role on both sides of
  the store's allowlist test, making that test vacuous.

None of these tests runs a protected stage, a pilot task or model inference.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train import f2_preflight as pf  # noqa: E402

#: The registered chains live outside the repository (governed evidence).
STORE = pathlib.Path(r"C:\Users\rober\F2_EVIDENCE\s8\trusted_store")
LEDGERS = [STORE / "s8_pilot_ledger.jsonl", STORE / "s8_pilot_ledger_corrected.jsonl"]
_have_ledgers = all(p.is_file() for p in LEDGERS)


def _by(report):
    return {f["id"]: f for f in report["findings"]}


# --------------------------------------------------------------------------- D1
class TestD1PreflightEvidenceSources:
    def test_defaults_are_unchanged_and_still_blocked(self):
        """With no sources supplied the gates keep their fail-closed defaults."""
        by = _by(pf.run_preflight())
        assert by["evidence.s8_base_gold"]["category"] == pf.EXTERNAL_EVIDENCE
        assert by["evidence.s8_base_gold"]["status"] == "absent"
        assert by["population.admission"]["category"] == pf.EXTERNAL_EVIDENCE
        assert by["population.admission"]["status"] == "insufficient"
        assert by["evidence.active_lesson"]["status"] == "absent"

    @pytest.mark.skipif(not _have_ledgers, reason="registered chains absent")
    def test_real_ledgers_report_the_counting_rule_figures(self):
        """A supplied ledger is evaluated by the reconciler, not by a literal."""
        tasks = sorted({
            json.loads(line)["task_id"]
            for p in LEDGERS
            for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
        })
        rep = pf.run_preflight(ledgers=LEDGERS, registered_tasks=tasks)
        adm = _by(rep)["population.admission"]
        assert "credited union 6" in adm["detail"]
        assert "remaining to 300 = 294" in adm["detail"]
        # 6 < 300, so the gate is honestly still insufficient -- not satisfied.
        assert adm["status"] == "insufficient"
        assert rep["result"] == "BLOCKED"

    @pytest.mark.skipif(not _have_ledgers, reason="registered chains absent")
    def test_a_ledger_count_does_not_establish_base_gold_provenance(self):
        """The anti-shortcut rule: admission counts never clear S8 provenance."""
        tasks = sorted({
            json.loads(line)["task_id"]
            for p in LEDGERS
            for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
        })
        rep = pf.run_preflight(ledgers=LEDGERS, registered_tasks=tasks)
        gold = _by(rep)["evidence.s8_base_gold"]
        assert gold["status"] == "absent"
        assert gold["category"] == pf.EXTERNAL_EVIDENCE

    def test_malformed_ledger_fails_closed(self, tmp_path):
        bad = tmp_path / "s8_pilot_ledger.jsonl"  # right NAME, broken content
        bad.write_text('{"seq": 0, "task_id": "x"\n', encoding="utf-8")
        rep = pf.run_preflight(ledgers=[bad], registered_tasks=["x"])
        adm = _by(rep)["population.admission"]
        assert adm["status"] == "unverifiable"
        assert adm["category"] == pf.EXTERNAL_EVIDENCE
        assert rep["result"] == "BLOCKED"

    def test_missing_ledger_fails_closed(self, tmp_path):
        rep = pf.run_preflight(ledgers=[tmp_path / "nope.jsonl"],
                               registered_tasks=["x"])
        assert _by(rep)["population.admission"]["status"] == "unverifiable"
        assert rep["result"] == "BLOCKED"

    def test_ledger_without_registered_tasks_fails_closed(self):
        rep = pf.run_preflight(ledgers=[STORE / "s8_pilot_ledger.jsonl"])
        adm = _by(rep)["population.admission"]
        assert adm["status"] == "unusable"
        assert rep["result"] == "BLOCKED"

    def test_unverifiable_s8_evidence_fails_closed(self, tmp_path):
        ev = tmp_path / "ev.json"
        ev.write_text(json.dumps({"tasks": [
            {"task_id": "t", "repository": "r", "base_commit": "c",
             "base_evidence": {"schema": "nope"},
             "gold_evidence": {"schema": "nope"}}]}), encoding="utf-8")
        rep = pf.run_preflight(s8_evidence=ev)
        gold = _by(rep)["evidence.s8_base_gold"]
        assert gold["status"] == "unverified"
        assert gold["category"] == pf.EXTERNAL_EVIDENCE

    def test_empty_s8_evidence_is_unusable_not_present(self, tmp_path):
        ev = tmp_path / "empty.json"
        ev.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        assert _by(pf.run_preflight(s8_evidence=ev))[
            "evidence.s8_base_gold"]["status"] == "unusable"

    def test_unreadable_s8_evidence_fails_closed(self, tmp_path):
        rep = pf.run_preflight(s8_evidence=tmp_path / "nope.json")
        assert _by(rep)["evidence.s8_base_gold"]["status"] == "unreadable"

    def test_cli_requires_registered_tasks_with_a_ledger(self, capsys):
        rc = pf.main(["--admission-ledger", "X"])
        assert rc == 2
        assert "requires --registered-tasks" in capsys.readouterr().err


# --------------------------------------------------------------------------- D2
class TestD2CapacityAdmissionAccounting:
    def _accounting(self, census=0):
        from qwen_train.f2_reconcile import _admission_accounting
        return _admission_accounting(census)

    @pytest.mark.skipif(not _have_ledgers, reason="registered chains absent")
    def test_six_concepts_are_reported_separately(self):
        a = self._accounting(0)
        assert a["census_admitted"] == 0
        assert a["operative_ledger_admissions"] == 5
        assert a["corrective_ledger_admissions"] == 1
        assert a["credited_admission_union"] == 6
        assert a["remaining_to_target"] == 294
        assert a["target"] == 300
        assert a["basis"] == "docs/EXPERIMENT_J_F2_ADMISSION_COUNTING_RULE.md"

    @pytest.mark.skipif(not _have_ledgers, reason="registered chains absent")
    def test_no_double_counting_across_the_corrective_chain(self):
        a = self._accounting(0)
        assert (a["operative_ledger_admissions"]
                + a["corrective_ledger_admissions"]
                == a["credited_admission_union"])

    def test_unmeasured_is_none_never_a_defaulted_zero(self, monkeypatch, tmp_path):
        """'not measured' must not be laundered into 'zero admitted'."""
        monkeypatch.setenv("SWARM_F2_TRUSTED_STORE", str(tmp_path / "absent"))
        a = self._accounting(0)
        assert a["credited_admission_union"] is None
        assert a["remaining_to_target"] is None
        assert a["unavailable_reason"]


# --------------------------------------------------------------------------- D3
class TestD3RefsRoleHardening:
    def test_refs_ok_passes_the_true_role_and_a_closed_allowlist(self):
        """Structural guard: the role must come from the key, not a constant."""
        src = (REPO / "qwen_train" / "f2_s8_compose.py").read_text(encoding="utf-8")
        start = src.index("def _refs_ok")
        body = src[start:src.index("def verify_evidence", start)]
        assert "refs or {}).items()" in body, "must iterate the role->ref pairs"
        assert "role=str(role)" in body, "the true key must be the role"
        assert "REQUIRED_EVIDENCE_ROLES" in body, "allowlist must be closed"
        assert "ROLE_TEST_OUTPUT" not in body, "must not pin one role"

    def test_a_role_outside_the_closed_allowlist_is_rejected(self, tmp_path):
        from qwen_train.f2_evidence import ArtifactRef
        from qwen_train.f2_governance import RetentionPolicy, TrustedArtifactStore
        from qwen_train.f2_s8_factory import REQUIRED_EVIDENCE_ROLES
        store = TrustedArtifactStore(
            root=tmp_path,
            retention=RetentionPolicy(policy_id="test", min_retention_days=0,
                                      immutable=False),
        )
        ref = ArtifactRef(name="x", digest="a" * 64, size_bytes=1,
                          role="not_a_required_role")
        ok, why = store.verify_ref(ref, allowed_roles=tuple(REQUIRED_EVIDENCE_ROLES))
        assert not ok and "not one of" in why

    def test_every_required_role_is_inside_the_closed_allowlist(self):
        from qwen_train.f2_s8_factory import REQUIRED_EVIDENCE_ROLES
        assert set(REQUIRED_EVIDENCE_ROLES) == {
            "base_report", "gold_report", "base_run_log", "gold_run_log",
            "evaluator_implementation", "base_evidence_record",
            "gold_evidence_record",
        }
