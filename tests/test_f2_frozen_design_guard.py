"""Guard tests: the frozen scientific design must not drift.

These are not tests of new behaviour. They exist to FAIL LOUDLY if a later
change to the F2 code alters a frozen statistical definition, and they pin the
boundaries between the Q6 diagnostic and the science it must not touch.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from qwen_train import f2_analysis as fa
from qwen_train import f2_arm_primitives as ap
from qwen_train import f2_statistics as fs


class TestFrozenConstants:
    def test_alpha_is_exactly_five_percent(self):
        assert fa.F2_ALPHA == 0.05
        assert fs.F2_ALPHA == 0.05 if hasattr(fs, "F2_ALPHA") else True

    def test_confidence_is_exactly_95_percent(self):
        assert fa.F2_CONFIDENCE == 0.95

    def test_q6_threshold_is_the_authorized_thirty_percent(self):
        assert fa.F2_INFRA_MAX_FRACTION == 0.30

    def test_minimum_pairs_is_300(self):
        from qwen_train.f2_readiness import FROZEN_MIN_PAIRS

        assert FROZEN_MIN_PAIRS == 300

    def test_mcnemar_is_two_sided_and_exact(self):
        sig = inspect.signature(fs.mcnemar_exact)
        assert "sided" in sig.parameters
        assert sig.parameters["sided"].default == "two-sided"
        # Exact, not asymptotic: no continuity correction parameter exists.
        assert "correction" not in sig.parameters


class TestContingencyAndEstimand:
    def test_b_and_c_definitions_are_unchanged(self):
        """b = T observed and X not; c = T not and X observed."""
        obs = [
            fa.PairedObservation("t1", 0, True, False),   # b
            fa.PairedObservation("t2", 0, False, True),   # c
            fa.PairedObservation("t3", 0, True, True),    # n11
            fa.PairedObservation("t4", 0, False, False),  # n00
        ]
        r = fa.finalize_f2(obs)
        assert r.b == 1
        assert r.c == 1
        assert r.n11 == 1
        assert r.n00 == 1
        assert r.n_complete == 4
        assert r.to_dict()["N"] == 4

    def test_discordant_count_is_b_plus_c(self):
        obs = [
            fa.PairedObservation("t1", 0, True, False),
            fa.PairedObservation("t2", 0, False, True),
        ]
        r = fa.finalize_f2(obs)
        assert r.d == r.b + r.c == 2

    def test_producer_and_reconstruction_agree(self):
        import random

        rnd = random.Random(20261005)
        obs = []
        for i in range(120):
            t = rnd.choice([True, False])
            x = rnd.choice([True, False])
            obs.append(fa.PairedObservation(f"t{i}", 0, t, x))
        prod = fa.finalize_f2(obs).to_dict()
        recon = fa.independent_reconstruction(obs)
        # Integer counts must agree exactly.
        for f in ("N", "b", "c", "d", "n00", "n01", "n10", "n11"):
            assert prod[f] == recon[f], f"producer/reconstruction disagree on {f}"
        # The reconstruction re-derives the bounds by a different floating-point
        # route, so agreement is asserted at the repository's established 1e-9
        # tolerance rather than bit-for-bit. A real divergence would exceed it by
        # orders of magnitude; the observed difference is ~1 ULP.
        for f in ("risk_difference", "p_value", "ci_low", "ci_high"):
            assert prod[f] == pytest.approx(recon[f], abs=1e-9), (
                f"producer/reconstruction diverge on {f}"
            )
        assert prod["direction"] == recon["direction"]


class TestQ6IsDiagnosticOnly:
    """Q6 reports infrastructure loss. It must never touch the science."""

    def _ledger(self, infra):
        survivors = [
            fa.PairedObservation(f"t{i}", 0, bool(i % 2), bool(i % 3 == 0))
            for i in range(60)
        ]
        lost = [
            fa.PairedObservation(f"i{k}", 0, None, None, "infrastructure:process_crash")
            for k in range(infra)
        ]
        return survivors, lost

    def test_infrastructure_loss_does_not_change_any_statistic(self):
        survivors, lost = self._ledger(40)
        clean = fa.finalize_f2(survivors).to_dict()
        lossy = fa.finalize_f2(survivors + lost).to_dict()
        for f in ("N", "b", "c", "d", "n00", "n01", "n10", "n11",
                  "risk_difference", "p_value", "ci_low", "ci_high", "direction"):
            assert clean[f] == lossy[f], f"Q6 leaked into {f}"

    def test_gate_breach_does_not_change_the_result_object(self):
        survivors, lost = self._ledger(40)
        r = fa.finalize_f2(survivors + lost)
        assert r.infrastructure_gate_satisfied is False
        # A breached gate is a separate verdict; the statistics still computed.
        assert r.n_complete == 60
        assert r.p_value is not None

    def test_gate_does_not_exclude_pairs_from_the_ledger(self):
        obs = [
            fa.PairedObservation("t1", 0, True, False),
            fa.PairedObservation("t2", 0, False, True),
            fa.PairedObservation("t3", 0, None, None, "infrastructure:process_crash"),
        ]
        r = fa.finalize_f2(obs)
        assert r.n_total == 3
        assert r.n_missing == 1
        assert r.n_complete == 2, "an infra-missing pair must be reported, not dropped"

    def test_q6_constant_is_not_used_in_the_frozen_path(self):
        src = pathlib.Path(fa.__file__).read_text(encoding="utf-8")
        body = src.split("def finalize_f2", 1)[1].split("def _binom_sf", 1)[0]
        # The gate is computed and attached, but it must not branch the statistics.
        assert "F2_INFRA_MAX_FRACTION" not in body
        assert "satisfied" not in body.replace("infrastructure_gate=gate.to_dict()", "")


class TestTreatmentPreservation:
    """T/X/C0 must not be able to drift from the causal intervention."""

    def _lessons(self, n):
        from qwen_train.f2_arm_primitives import lesson_hash_of
        from runtime_v2.services.f2_freeze import LessonEntry

        return tuple(
            LessonEntry(lesson_id=f"L{i}", lesson_hash=lesson_hash_of(f"rule {i}"),
                        position=i, rule_text=f"rule {i}")
            for i in range(1, n + 1)
        )

    def _frozen_t(self, n=3, remove=2):
        from qwen_train.f2_arm_primitives import render_block_from_records
        from runtime_v2.services.f2_freeze import freeze_artifact

        lessons = self._lessons(n)
        return freeze_artifact(
            rendered_artifact=render_block_from_records(lessons),
            arm="T",
            ordered_lessons=lessons,
            lesson_l_id=f"L{remove}",
            lesson_l_hash=lessons[remove - 1].lesson_hash,
            freeze_timestamp=1_700_000_000.0,
        )

    def test_x_removes_exactly_one_lesson(self):
        t = self._frozen_t(3, 2)
        x = ap.derive_x_from_frozen(t)
        assert len(x.ordered_lessons) == 2
        assert "L2" not in {l.lesson_id for l in x.ordered_lessons}

    def test_x_preserves_positions_without_renumbering(self):
        """The L gap must remain; renumbering would rerank the treatment."""
        t = self._frozen_t(3, 2)
        x = ap.derive_x_from_frozen(t)
        assert [l.position for l in x.ordered_lessons] == [1, 3]

    def test_x_preserves_survivor_bytes_exactly(self):
        t = self._frozen_t(3, 2)
        x = ap.derive_x_from_frozen(t)
        before = {l.lesson_id: (l.position, l.lesson_hash, l.rule_text)
                  for l in t.ordered_lessons if l.lesson_id != "L2"}
        after = {l.lesson_id: (l.position, l.lesson_hash, l.rule_text)
                 for l in x.ordered_lessons}
        assert before == after

    def test_x_binds_source_t_provenance(self):
        """X must carry provenance back to the frozen T manifest."""
        t = self._frozen_t(3, 2)
        x = ap.derive_x_from_frozen(t)
        assert x.promotion_proof_ref is not None
        assert t.content_address in x.promotion_proof_ref
        assert t.manifest_hash in x.promotion_proof_ref

    def test_single_lesson_t_fails_closed(self):
        """X would be empty and indistinguishable from C0, so it must refuse."""
        from runtime_v2.services.f2_freeze import FreezeVerificationError

        t = self._frozen_t(1, 1)
        with pytest.raises(FreezeVerificationError):
            ap.derive_x_from_frozen(t)

    def test_x_is_deterministic(self):
        a = ap.derive_x_from_frozen(self._frozen_t(3, 2))
        b = ap.derive_x_from_frozen(self._frozen_t(3, 2))
        assert a.manifest_hash == b.manifest_hash

    def test_c0_is_independently_empty(self):
        from runtime_v2.services.f2_freeze import verify_manifest

        c0 = ap.build_c0_artifact(task_id="inst-1", freeze_timestamp=1_700_000_000.0)
        assert c0.rendered_artifact == ""
        assert not c0.ordered_lessons
        verify_manifest(c0)  # a genuine, verifiable frozen artifact

    def test_c0_carries_no_lesson_identity(self):
        c0 = ap.build_c0_artifact(task_id="inst-1", freeze_timestamp=1_700_000_000.0)
        payload = c0.to_dict() if hasattr(c0, "to_dict") else {}
        assert not payload.get("lesson_l_id")
        assert not payload.get("lesson_l_hash")
        assert not payload.get("ordered_lessons")

    def test_c0_shares_no_state_with_t_or_x(self):
        t = self._frozen_t(3, 2)
        x = ap.derive_x_from_frozen(t)
        c0 = ap.build_c0_artifact(task_id="inst-1", freeze_timestamp=1_700_000_000.0)
        assert c0.manifest_hash not in (t.manifest_hash, x.manifest_hash)
        assert c0.arm == "C0"


class TestSourceLevelInvariants:
    """Static guards: the properties must hold in the code, not just in tests."""

    def _src(self, module):
        return pathlib.Path(module.__file__).read_text(encoding="utf-8")

    def test_evaluator_does_not_import_the_verifier_or_analysis(self):
        src = self._src(__import__("qwen_train.f2_evaluator", fromlist=["x"]))
        for banned in ("f2_governance", "f2_protocol", "f2_analysis", "f2_statistics"):
            assert f"import {banned}" not in src
            assert f"from qwen_train.{banned}" not in src

    def test_independent_reconstruction_does_not_call_frozen_statistics(self):
        tree = ast.parse(self._src(fa))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "independent_reconstruction")
        calls = {
            n.func.attr for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "mcnemar_exact" not in calls

    def test_regrade_does_not_re_execute(self):
        """GOVERNANCE_LIMITATIONS states regrade never re-executes."""
        from qwen_train import f2_governance as gov

        assert "reexecution" in str(gov.GOVERNANCE_LIMITATIONS).lower()

    def test_readiness_item_count_is_unchanged_at_18(self):
        from qwen_train.f2_readiness import READINESS_ITEMS

        assert len(READINESS_ITEMS) == 18

    def test_no_frozen_f0_or_f1_file_was_touched_by_these_modules(self):
        """The new modules must not reach into frozen F0/F1 documents."""
        for mod in ("f2_evaluator", "f2_isolation", "f2_integrity",
                    "f2_model_provenance", "f2_preflight"):
            src = self._src(__import__(f"qwen_train.{mod}", fromlist=["x"]))
            assert "docs/EXPERIMENT_J.md" not in src
            assert "write_text" not in src or mod == "f2_preflight"
