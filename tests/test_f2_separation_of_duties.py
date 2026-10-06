"""Separation-of-duties enforcement for F2 governance records.

Required by ``EXPERIMENT_J_F2_OPERATOR_HANDOFF.md:655-656`` and clarified at
``:658-660`` ("the person authorizing the run must not also be the sole judge of
that run's exclusions"). Before this existed the requirement was prose only and
nothing checked it. These tests pin the mechanical behaviour: it fails closed when
absent, refuses an operator who is the sole judge of their own exclusions, and
allows that overlap only when the pre-specified mechanical rule set decides.

No experiment step is executed and no test writes production state.
"""
from __future__ import annotations

import pytest

from qwen_train.f2_governance import (
    SeparationOfDuties,
    verify_separation_of_duties,
)

VALID = SeparationOfDuties(
    run_authority="experiment-authority",
    operator="alice",
    exclusion_adjudicator="bob",
    independent_regrade="carol",
    q5_q6_adjudicator="dave",
)


class TestFailsClosedWhenAbsent:
    def test_none_is_never_acceptable(self):
        ok, detail = verify_separation_of_duties(None)
        assert ok is False
        assert "NOT ESTABLISHED" in detail

    @pytest.mark.parametrize(
        "field",
        [
            "run_authority",
            "operator",
            "exclusion_adjudicator",
            "independent_regrade",
            "q5_q6_adjudicator",
        ],
    )
    def test_every_role_is_required(self, field):
        payload = VALID.to_dict()
        payload[field] = ""
        ok, detail = verify_separation_of_duties(payload)
        assert ok is False
        assert field in detail

    def test_blank_whitespace_is_not_a_role(self):
        payload = VALID.to_dict()
        payload["operator"] = "   "
        assert verify_separation_of_duties(payload)[0] is False


class TestOperatorMayNotBeTheSoleJudge:
    def test_operator_as_adjudicator_is_refused(self):
        rec = SeparationOfDuties(
            run_authority="a",
            operator="alice",
            exclusion_adjudicator="alice",
            independent_regrade="carol",
            q5_q6_adjudicator="dave",
        )
        ok, detail = verify_separation_of_duties(rec)
        assert ok is False
        assert "sole exclusion adjudicator" in detail

    def test_operator_may_adjudicate_when_rules_are_mechanical(self):
        """The handoff permits overlap if exclusions are decided by pre-specified
        rules rather than by that person's judgement."""
        rec = SeparationOfDuties(
            run_authority="a",
            operator="alice",
            exclusion_adjudicator="alice",
            independent_regrade="carol",
            q5_q6_adjudicator="dave",
            mechanical_exclusion_rules=True,
        )
        ok, detail = verify_separation_of_duties(rec)
        assert ok is True
        assert "mechanical_rules=True" in detail

    def test_operator_may_not_also_sign_the_independent_regrade(self):
        rec = SeparationOfDuties(
            run_authority="a",
            operator="alice",
            exclusion_adjudicator="bob",
            independent_regrade="alice",
            q5_q6_adjudicator="dave",
        )
        ok, detail = verify_separation_of_duties(rec)
        assert ok is False
        assert "no independent verification" in detail

    def test_one_party_cannot_judge_and_sign(self):
        rec = SeparationOfDuties(
            run_authority="a",
            operator="alice",
            exclusion_adjudicator="bob",
            independent_regrade="bob",
            q5_q6_adjudicator="dave",
        )
        ok, detail = verify_separation_of_duties(rec)
        assert ok is False
        assert "adjudicates exclusions" in detail


class TestAcceptedSeparations:
    def test_distinct_parties_pass(self):
        ok, detail = verify_separation_of_duties(VALID)
        assert ok is True
        assert "alice" in detail and "bob" in detail and "carol" in detail

    def test_mapping_and_dataclass_agree(self):
        assert verify_separation_of_duties(VALID.to_dict())[0] is (
            verify_separation_of_duties(VALID)[0]
        )

    def test_record_serialises_every_role(self):
        payload = VALID.to_dict()
        for field in (
            "run_authority",
            "operator",
            "exclusion_adjudicator",
            "independent_regrade",
            "q5_q6_adjudicator",
            "mechanical_exclusion_rules",
        ):
            assert field in payload

    def test_identity_comparison_is_exact_not_fuzzy(self):
        """'alice' and 'Alice' are different people as far as this check knows."""
        rec = SeparationOfDuties(
            run_authority="a",
            operator="alice",
            exclusion_adjudicator="Alice",
            independent_regrade="carol",
            q5_q6_adjudicator="dave",
        )
        assert verify_separation_of_duties(rec)[0] is True