"""F2 confirmatory analysis: ledger -> final result, and independent audit.

Proves the final link of the F2 chain (F2-IMPL-AUTH-019):
* ``finalize_f2`` produces the frozen result from the paired ledger;
* ``independent_reconstruction`` re-derives the same numbers WITHOUT calling
  ``f2_statistics`` (a genuine second implementation, not a wrapper);
* missingness is reported, never converted into success/failure.

No network, no services, no real evidence.
"""
from __future__ import annotations

import pytest

from qwen_train.f2_analysis import (
    DESIGN_CLASS_CONFIRMATORY,
    DESIGN_CLASS_EXPLORATORY,
    FROZEN_MIN_PAIRS,
    PairedObservation,
    finalize_f2,
    independent_reconstruction,
)

_CONFIRM = DESIGN_CLASS_CONFIRMATORY
_CLEAN = "POST_CUTOFF"
_HASH64 = "a" * 64


def _pairs(b, c, both, neither):
    out = []
    out += [(True, False)] * b
    out += [(False, True)] * c
    out += [(True, True)] * both
    out += [(False, False)] * neither
    return out


FIXTURES = [
    (3, 1, 2, 4),
    (6, 14, 0, 0),
    (10, 10, 0, 0),
    (5, 0, 5, 0),
    (0, 3, 7, 0),
    (0, 0, 8, 8),
    (4, 0, 6, 0),
]


class TestFinalize:
    def test_contingency_conventions(self):
        r = finalize_f2(_pairs(3, 1, 2, 4))
        assert (r.n10, r.n01, r.n11, r.n00) == (3, 1, 2, 4)
        assert (r.b, r.c, r.d) == (3, 1, 4)
        assert r.n_complete == 10
        assert r.n_total == 10 and r.n_missing == 0
        assert r.risk_difference == pytest.approx(0.2)
        assert r.direction == "favours_treatment"
        assert r.sid == "two-sided"

    def test_matches_the_authoritative_statistics(self):
        from qwen_train.f2_statistics import mcnemar_exact

        for b, c, both, neither in FIXTURES:
            pairs = _pairs(b, c, both, neither)
            r = finalize_f2(pairs)
            ref = mcnemar_exact([t for t, _ in pairs], [x for _, x in pairs])
            assert r.p_value == pytest.approx(ref.p_value)
            assert r.ci_low == pytest.approx(ref.ci_low)
            assert r.ci_high == pytest.approx(ref.ci_high)
            assert r.risk_difference == pytest.approx(ref.risk_difference)
            assert (r.b, r.c, r.d) == (ref.b, ref.c, ref.b + ref.c)

    def test_r_transformed_oracle(self):
        # b=6, c=14, N=20 -> R binom.test(6,20)$conf.int (0.1189, 0.5428) -> RD
        r = finalize_f2(_pairs(6, 14, 0, 0))
        assert r.ci_low == pytest.approx(2 * 0.1189 - 1.0, abs=1e-3)
        assert r.ci_high == pytest.approx(2 * 0.5428 - 1.0, abs=1e-3)

    def test_to_dict_reports_the_required_fields(self):
        d = finalize_f2(_pairs(2, 1, 1, 4)).to_dict()
        for key in ("N", "n00", "n01", "n10", "n11", "b", "c", "d",
                    "risk_difference", "p_value", "ci_low", "ci_high",
                    "alpha", "confidence"):
            assert key in d


class TestMissingness:
    def test_incomplete_pairs_are_reported_not_dropped_as_outcomes(self):
        obs = _pairs(3, 1, 2, 4) + [
            PairedObservation("t9", 0, None, True, "infrastructure:trajectory_malformed"),
            PairedObservation("t9", 1, True, None, "infrastructure:workspace_preparation_failed"),
            PairedObservation("t9", 2, None, None, "scientific:no_event_stream"),
        ]
        r = finalize_f2(obs)
        assert r.n_total == 13
        assert r.n_complete == 10
        assert r.n_missing == 3
        assert r.missing_by_reason["infrastructure:trajectory_malformed"] == 1
        assert r.missing_by_reason["scientific:no_event_stream"] == 1
        # The complete-pair statistics are UNCHANGED by the missing rows.
        assert (r.b, r.c, r.d) == (3, 1, 4)
        assert r.risk_difference == pytest.approx(0.2)

    def test_missing_is_not_treated_as_failure(self):
        # If missing were coerced to False, b would change; it must not.
        only_missing = [PairedObservation("t", 0, None, True, "x")]
        r = finalize_f2(only_missing)
        assert r.n_complete == 0
        assert r.b == 0 and r.c == 0


class TestIndependentReconstruction:
    def test_matches_finalize_on_all_fixtures(self):
        for b, c, both, neither in FIXTURES:
            pairs = _pairs(b, c, both, neither)
            prod = finalize_f2(pairs)
            ind = independent_reconstruction(pairs)
            assert ind["N"] == prod.n_complete
            assert ind["b"] == prod.b and ind["c"] == prod.c and ind["d"] == prod.d
            assert ind["risk_difference"] == pytest.approx(prod.risk_difference, abs=1e-9)
            assert ind["p_value"] == pytest.approx(prod.p_value, abs=1e-9)
            assert ind["ci_low"] == pytest.approx(prod.ci_low, abs=1e-6)
            assert ind["ci_high"] == pytest.approx(prod.ci_high, abs=1e-6)

    def test_does_not_call_the_production_statistics(self, monkeypatch):
        import qwen_train.f2_statistics as fs

        def boom(*a, **k):  # noqa: ANN001
            raise AssertionError("independent path must not call f2_statistics")

        monkeypatch.setattr(fs, "mcnemar_exact", boom)
        # Independent path still works...
        out = independent_reconstruction(_pairs(5, 0, 5, 0))
        assert out["risk_difference"] == pytest.approx(0.5)
        assert out["ci_low"] == pytest.approx(-0.0218, abs=1e-3)
        # ...and the production path genuinely depends on the frozen function.
        with pytest.raises(AssertionError):
            finalize_f2(_pairs(5, 0, 5, 0))

    def test_zero_complete_pairs_is_conservative(self):
        ind = independent_reconstruction([])
        assert ind["N"] == 0
        assert ind["p_value"] == 1.0
        assert ind["ci_low"] == -1.0 and ind["ci_high"] == 1.0

    def test_coercion_of_tuples_mappings_and_objects(self):
        a = finalize_f2([(True, False), {"t_endpoint": True, "x_endpoint": False}])
        b = finalize_f2([PairedObservation("t", 0, True, False)])
        assert a.to_dict()["risk_difference"] == b.to_dict()["risk_difference"]


class TestExperimentalUnit:
    """ONE PAIR = ONE TASK (F2-IMPL-AUTH-024 s3 and s6.3).

    The unit of analysis is the task: one T rollout and one X rollout. A
    repeated run of the same task is a replicate inside a single clustered unit
    and "MUST NOT be counted as additional independent pairs". Counting it would
    inflate ``m`` and break the independence the exact McNemar null assumes, so
    the ledger refuses rather than silently keeps or drops the duplicate --
    choosing a survivor is a Q5 exclusion decision, and Q5 requires exclusions
    to be pre-specified and outcome-independent.
    """

    def test_two_complete_pairs_for_one_task_fail_closed(self):
        dup = [
            PairedObservation("t1", 0, True, False),
            PairedObservation("t1", 1, False, True),  # same task, different seed
        ]
        with pytest.raises(ValueError, match="one pair = one task"):
            finalize_f2(dup)

    def test_the_independent_auditor_refuses_the_same_ledger(self):
        """Producer and auditor must enforce the unit identically."""
        dup = [
            PairedObservation("t1", 0, True, False),
            PairedObservation("t1", 1, True, False),
        ]
        with pytest.raises(ValueError, match="pseudo-replication"):
            independent_reconstruction(dup)

    def test_repeated_attempts_pass_while_only_one_pair_completes(self):
        """Q6 reruns retain every attempt; only one may reach a complete pair."""
        obs = [
            PairedObservation("t1", 0, None, None, "infrastructure:process_crash"),
            PairedObservation("t1", 1, None, True, "infrastructure:trajectory_malformed"),
            PairedObservation("t1", 2, True, False),
        ]
        r = finalize_f2(obs)
        assert r.n_total == 3
        assert r.n_complete == 1
        assert r.n_missing == 2

    def test_the_identity_free_coercion_path_still_pairs_up(self):
        # Tuple/map coercion carries no task_id, so there is no identity to collide.
        r = finalize_f2([(True, False), (False, True), (True, True)])
        assert r.n_complete == 3

    def test_m_counts_distinct_tasks_whatever_the_seed(self):
        obs = [
            PairedObservation(f"task-{i}", i, i % 2 == 0, i % 3 == 0)
            for i in range(6)
        ]
        r = finalize_f2(obs)
        assert r.n_complete == 6
        assert r.n_complete == len({o.task_id for o in obs})

    def test_a_missing_duplicate_never_trips_the_rule(self):
        """Identity collisions among missing rows are reported, not refused."""
        obs = [
            PairedObservation("t1", 0, None, None, "infrastructure:process_crash"),
            PairedObservation("t1", 1, None, None, "infrastructure:qdrant_unavailable"),
        ]
        r = finalize_f2(obs)
        assert r.n_total == 2 and r.n_complete == 0 and r.n_missing == 2


# ---------------------------------------------------------------------------
# F2-IMPL-AUTH-024 s4 / s6.1 / s6.6  -  the confirmatory trust boundary.
#
# These exercise the REAL entry point (finalize_f2 / independent_reconstruction),
# never a helper that builds trusted objects without traversing it. What they
# prove is REPOSITORY ENFORCEMENT: a declared confirmatory claim must clear task
# identity, S10 contamination, retained-evidence identity, analyzable N and Q6.
# They do NOT prove authority - design_class is a declared label, not a grant.
# ---------------------------------------------------------------------------
def _confirmatory_unit(
    task_id, *, seed=0, state=_CLEAN, evidence=_HASH64, t=True, x=False
):
    return PairedObservation(task_id, seed, t, x, "", state, evidence)


def _confirmatory_ledger(n=FROZEN_MIN_PAIRS, **kw):
    return [_confirmatory_unit(f"task-{i}", seed=i, **kw) for i in range(n)]


class TestConfirmatoryTrustBoundary:
    """Arbitrary caller input must not be able to produce a confirmatory claim."""

    def test_bare_tuples_cannot_carry_a_confirmatory_claim(self):
        """Attack: caller-created PairedObservation-by-tuple with no identity."""
        with pytest.raises(ValueError, match="no task_id"):
            finalize_f2([(True, False)] * FROZEN_MIN_PAIRS, design_class=_CONFIRM)

    def test_no_eligibility_object_is_accepted_by_the_analysis(self):
        """Attack: a forged eligibility decision has nowhere to be passed."""
        import inspect

        from qwen_train.f2_admission import F2Admission

        params = inspect.signature(finalize_f2).parameters
        assert not any(
            "eligib" in p or "admission" in p or "decision" in p for p in params
        ), "finalize_f2 must consume observations, never a caller verdict"
        forged = F2Admission(
            admissible=True,
            detail="forged",
            readiness={},
            protocol_state="STATE_VERIFIED",
            clean_room_ok=True,
            results={},
        )
        with pytest.raises(TypeError):
            finalize_f2([forged], design_class=_CONFIRM)

    def test_an_unrecognised_design_class_fails_closed(self):
        """Attack: unauthorized design class ('CONFIRMATORY' != 'confirmatory')."""
        with pytest.raises(ValueError, match="unknown design class"):
            finalize_f2(_confirmatory_ledger(), design_class="CONFIRMATORY")
        with pytest.raises(ValueError, match="unknown design class"):
            finalize_f2(_confirmatory_ledger(), design_class=None)

    def test_unknown_contamination_is_refused(self):
        """UNKNOWN is NOT CLEAN for confirmatory inference (AUTH-028)."""
        obs = _confirmatory_ledger()
        obs[-1] = _confirmatory_unit("task-299", state="NOT_DECLARED")
        with pytest.raises(ValueError, match="NOT CLEAN"):
            finalize_f2(obs, design_class=_CONFIRM)

    def test_every_non_clean_contamination_state_is_refused(self):
        for state in ("NOT_DECLARED", "NO_DATE", "PRE_CUTOFF", ""):
            obs = _confirmatory_ledger()
            obs[0] = _confirmatory_unit("task-0", state=state)
            with pytest.raises(ValueError, match="NOT CLEAN"):
                finalize_f2(obs, design_class=_CONFIRM)

    def test_an_undersized_confirmatory_population_is_refused(self):
        """AUTH-013: n = 300 ANALYZABLE paired tasks, not 300 admitted tasks."""
        with pytest.raises(ValueError, match="below the frozen minimum"):
            finalize_f2(_confirmatory_ledger(FROZEN_MIN_PAIRS - 1), design_class=_CONFIRM)

    def test_a_unit_without_an_evidence_identity_is_refused(self):
        """AUTH-019: the ledger analyses retained evidence, not bare booleans."""
        for bad in ("", "not-a-digest", "A" * 64, "a" * 63):
            obs = _confirmatory_ledger()
            obs[0] = _confirmatory_unit("task-0", evidence=bad)
            with pytest.raises(ValueError, match="evidence identity"):
                finalize_f2(obs, design_class=_CONFIRM)

    def test_duplicate_task_ids_are_refused_before_any_statistic(self):
        obs = _confirmatory_ledger(FROZEN_MIN_PAIRS - 1)
        obs.append(_confirmatory_unit("task-0"))
        with pytest.raises(ValueError, match="pseudo-replication"):
            finalize_f2(obs, design_class=_CONFIRM)

    def test_a_breached_q6_gate_refuses_a_confirmatory_claim(self):
        """AUTH-018: STOP-AND-DIAGNOSE is 'not a valid run'."""
        lost = [
            PairedObservation(
                f"infra-{k}", k, None, None, "infrastructure:process_crash"
            )
            for k in range(FROZEN_MIN_PAIRS // 2)
        ]
        with pytest.raises(ValueError, match="STOP-AND-DIAGNOSE"):
            finalize_f2(_confirmatory_ledger() + lost, design_class=_CONFIRM)

    def test_the_auditor_applies_the_identical_gate(self):
        """Producer and auditor must refuse the SAME ledger the same way."""
        for bad in (_confirmatory_ledger(FROZEN_MIN_PAIRS - 1), [
            _confirmatory_unit(f"task-{i}", state="NOT_DECLARED")
            for i in range(FROZEN_MIN_PAIRS)
        ]):
            with pytest.raises(ValueError, match="confirmatory eligibility"):
                finalize_f2(bad, design_class=_CONFIRM)
            with pytest.raises(ValueError, match="confirmatory eligibility"):
                independent_reconstruction(bad, design_class=_CONFIRM)

    def test_the_default_is_exploratory_and_is_labelled_as_such(self):
        """AUTH-024 s6: exploratory output must never be silently confirmatory."""
        r = finalize_f2(_pairs(3, 1, 2, 4))
        assert r.to_dict()["design_class"] == DESIGN_CLASS_EXPLORATORY
        assert r.to_dict()["design_class"] != DESIGN_CLASS_CONFIRMATORY
        assert r.p_value is not None

    def test_exploratory_keeps_unknown_contamination_and_small_n(self):
        """The UNKNOWN rule is confirmatory-specific; exploratory stays open."""
        r = finalize_f2(_confirmatory_ledger(4, state="NOT_DECLARED"))
        assert r.to_dict()["design_class"] == DESIGN_CLASS_EXPLORATORY
        assert r.n_complete == 4
        assert r.p_value is not None

    def test_valid_authorized_evidence_produces_a_labelled_confirmatory_result(self):
        r = finalize_f2(_confirmatory_ledger(), design_class=_CONFIRM)
        d = r.to_dict()
        assert d["design_class"] == DESIGN_CLASS_CONFIRMATORY
        assert d["N"] == FROZEN_MIN_PAIRS
        assert r.p_value is not None
        # The producer and the independent auditor agree on the gated ledger.
        recon = independent_reconstruction(_confirmatory_ledger(), design_class=_CONFIRM)
        assert recon["design_class"] == DESIGN_CLASS_CONFIRMATORY
        assert recon["N"] == d["N"]
        assert recon["p_value"] == pytest.approx(d["p_value"], abs=1e-9)

    def test_the_contamination_vocabulary_is_imported_not_redeclared(self):
        """Single source of truth: same object, not a copied mapping."""
        import qwen_train.f2_analysis as fa
        import qwen_train.f2_population as fp
        import qwen_train.f2_readiness as fr

        assert fa._CONTAMINATION_CLASS_MAP is fp._CONTAMINATION_CLASS
        assert fa.CONTAMINATION_CLEAN == fp.CONTAMINATION_CLEAN == "CLEAN"
        assert fa._CONTAMINATION_CLASS_MAP["NOT_DECLARED"] == "UNKNOWN"
        assert fa.FROZEN_MIN_PAIRS is fr.FROZEN_MIN_PAIRS == 300
