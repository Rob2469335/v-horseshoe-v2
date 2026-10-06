"""Documentation-consistency guards for Experiment J / F2.

These tests exist because a real contradiction was found during the 2026-10-05
reconciliation: the readiness plan's population section claimed "R8 is unmet for
every task" while its own rows and §4b said the ``relevant_file_set`` is derived for
12 of 14 tasks. Documentation drifts silently; tests do not.

Each assertion below pins a claim that was **false** and has been corrected, so the
stale wording cannot quietly return. They check wording and structure only -- they
cannot verify that prose is *true*, and they are not a substitute for running
``python -m qwen_train.f2_preflight``, which is the machine-readable authority.

A doc test that fails because someone improved the wording is not a reason to delete
it. Update the assertion to the better wording and say why in the commit.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(r"C:\Users\rober\Projects\v-horseshoe-v2")
DOCS = REPO / "docs"

READINESS = DOCS / "EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md"
DESIGN = DOCS / "EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md"
WORKER_AUTH = DOCS / "EXPERIMENT_J_F2_WORKER_EXECUTION_AUTHORIZATION.md"
TASK_CONTRACT = DOCS / "EXPERIMENT_J_TASK_READINESS_CONTRACT.md"
HANDOFF = DOCS / "EXPERIMENT_J_F2_OPERATOR_HANDOFF.md"
STATE = DOCS / "LEARNING_EXPERIMENT_STATE.md"
TOPOLOGY = DOCS / "INFERENCE_TOPOLOGY.md"

#: The vocabulary ``f2_preflight`` emits. A handoff step claiming a state outside
#: this set cannot be reconciled with the machine report by eye.
PREFLIGHT_STATES = frozenset(
    {
        "IMPLEMENTED",
        "READY",
        "OPERATOR ACTION REQUIRED",
        "PRIVILEGED HOST ACTION REQUIRED",
        "EXTERNAL EVIDENCE REQUIRED",
        "AUTHORIZATION REQUIRED",
        "NOT EXECUTED",
        "BLOCKED",
    }
)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


class TestHandoffExists:
    def test_handoff_is_present_and_non_trivial(self):
        assert HANDOFF.is_file(), "the single authoritative operator handoff is missing"
        assert len(read(HANDOFF).splitlines()) > 150

    def test_handoff_carries_all_sixteen_steps_in_order(self):
        text = read(HANDOFF)
        found = re.findall(r"^### ([A-P])\. ", text, re.MULTILINE)
        assert found == list("ABCDEFGHIJKLMNOP"), found

    def test_every_step_declares_owner_and_fail_closed(self):
        """A step without an owner and a fail-closed condition is not a handoff."""
        text = read(HANDOFF)
        blocks = re.split(r"^### (?=[A-P]\. )", text, flags=re.MULTILINE)[1:]
        assert len(blocks) == 16
        for letter, block in zip("ABCDEFGHIJKLMNOP", blocks):
            assert "**Owner:**" in block, f"step {letter} has no owner"
            assert "**Fail-closed" in block, f"step {letter} has no fail-closed condition"
            assert "**Next" in block or letter == "P", f"step {letter} has no next step"

    def test_every_step_status_comes_from_the_preflight_vocabulary(self):
        """A step's status must be reconcilable against the machine report.

        Compound statuses are allowed and often more honest than a flattened one
        (step G is genuinely both IMPLEMENTED and NOT EXECUTED), so every token
        separated by a comma or slash must itself be a vocabulary term.
        """
        text = read(HANDOFF)
        headers = re.findall(r"^### ([A-P])\. .*?— \*\*(.+?)\*\*", text, re.MULTILINE)
        assert len(headers) == 16, headers
        for letter, status in headers:
            for token in re.split(r"[,/]", status):
                token = token.strip()
                assert token in PREFLIGHT_STATES, (
                    f"step {letter} uses unknown status token {token!r} (from {status!r})"
                )

    def test_handoff_states_that_green_tests_are_not_readiness(self):
        assert "not experimental readiness" in read(HANDOFF).lower()

    def test_handoff_does_not_leak_a_secret_value(self):
        """It may name the receipt-key variable; it may never hold a value."""
        text = read(HANDOFF)
        assert "SWARM_RECEIPT_KEY" in text
        assert re.search(r"SWARM_RECEIPT_KEY\s*[=:]\s*\S", text) is None


class TestContradictionsStayFixed:
    """Claims that were false when found, and must not come back."""

    @pytest.mark.parametrize(
        "path",
        [READINESS, DESIGN, WORKER_AUTH, TASK_CONTRACT],
    )
    def test_r8_is_not_claimed_unmet(self, path):
        assert "R8 is unmet for every task" not in read(path)

    def test_arm_runner_is_not_claimed_absent(self):
        text = read(READINESS)
        assert "The arm runner is deliberately absent" not in text
        assert "arm runner is deliberately absent" not in text

    def test_bundle_wiring_is_not_claimed_outstanding(self):
        """The stale wording may survive only inside an attributed correction."""
        text = read(READINESS)
        offenders = [
            line
            for line in text.splitlines()
            if "Remaining wiring" in line
            and not any(sig in line for sig in ("previously", "Corrected", "closed by"))
        ]
        assert not offenders, offenders

    def test_execution_adapter_is_not_claimed_future(self):
        text = read(DESIGN)
        assert "f2_execution_adapter.py, FUTURE" not in text
        assert "Adapter design contract (future" not in text
        assert "Defined contract (NOT implemented)" not in text

    def test_result_derivation_protocol_is_not_claimed_a_governance_gap(self):
        text = read(READINESS)
        assert "the real result-derivation protocol and its format;" not in text

    def test_worker_auth_no_longer_claims_implementation_absent(self):
        assert "## 18. Implementation has NOT yet occurred" not in read(WORKER_AUTH)

    def test_task_contract_states_zero_valid_pairs(self):
        text = read(TASK_CONTRACT)
        assert "zero valid base/gold pairs" in text.lower()
        assert "R6, R7 and R8 are unmet" not in text


class TestNoFalseCapabilityClaims:
    def test_no_document_claims_egress_enforcement_on_this_host(self):
        """Q9 enforcement is privileged and absent. No doc may imply otherwise."""
        banned = re.compile(r"(egress|isolation|network)[^.\n]{0,80}\b(is enforced|enforced)\b", re.I)
        for path in (READINESS, HANDOFF, STATE):
            assert banned.search(read(path)) is None, f"{path.name} implies enforcement"

    def test_handoff_records_the_observed_reachable_result(self):
        text = read(HANDOFF)
        assert "NOT ESTABLISHED" in text
        assert "REACHABLE" in text

    def test_handoff_forbids_running_q13_on_green_gates(self):
        text = read(HANDOFF).lower()
        assert "merely because software gates are green" in text

    def test_population_admission_is_still_recorded_as_unauthorized(self):
        text = read(HANDOFF)
        assert "does not authorize" in text.lower() or "explicitly does not" in text.lower()

    def test_curator_is_never_named_as_if_appointed(self):
        """Authority is undefined; no document may imply an office exists.

        Q5/Q6 themselves ARE granted (F2-IMPL-AUTH-018), so this must not overstate
        the gap either -- the invariant is specifically about the curator office.
        """
        text = read(HANDOFF)
        low = text.lower()
        assert "no appointed curator" in low
        assert "do not let an agent or the operator appoint themselves" in low
        # And it must not overstate the gap either.
        assert "granted" in low


    def test_q5_and_q6_are_not_relisted_as_pending(self):
        """AUTH-018 (operator, 2026-10-05) GRANTED both.

        The readiness checklist previously still read "Ratify"/"Authorize", which
        made a settled decision look outstanding. Only the curator *office* is open.
        """
        text = read(READINESS)
        q5 = next(l for l in text.splitlines() if l.startswith("| **Q5**"))
        q6 = next(l for l in text.splitlines() if l.startswith("| **Q6**"))
        assert "RATIFIED" in q5 and "AUTH-018" in q5, q5
        assert "AUTHORIZED" in q6 and "AUTH-018" in q6, q6
        assert "not a statistical property" in q6.lower(), q6

    def test_handoff_does_not_call_q6_a_statistical_property(self):
        """AUTH-018: Q6 is an operational feasibility ceiling."""
        low = read(HANDOFF).lower()
        assert "q6 *statistical* gate" not in low
        assert "not a statistical property" in low


class TestConversionGapIsVisibleWhereItMatters:
    def test_topology_states_the_conversion_link_is_unrecorded(self):
        text = read(TOPOLOGY)
        assert "UNRECORDED" in text
        assert "timestamp ordering is not a conversion record" in text.lower()

    def test_topology_does_not_launder_the_legacy_inference(self):
        text = read(TOPOLOGY)
        assert "INFERRED" in text
        assert "AGENTS_LEGACY.md" in text

    def test_state_document_records_the_conversion_gap(self):
        assert "conversion" in read(STATE).lower()


class TestFrozenAndHistoricalAreNotRewritten:
    """F0's git history is verified outside pytest.

    ``git log`` needs a subprocess, and this repository's autouse
    ``global_subprocess_mock`` fixture blocks real ones. That guard exists for good
    reason and is NOT weakened here; F0's freeze commit is instead checked directly
    (``git log -1 -- docs/EXPERIMENT_J.md``) before commit, alongside the SHA-256
    check on the immutable legacy file below.
    """

    def test_legacy_agent_file_is_untouched_by_this_pass(self):
        import hashlib

        digest = hashlib.sha256((REPO / "AGENTS_LEGACY.md").read_bytes()).hexdigest().upper()
        assert digest == (
            "F0DDF84CC876EDD8574AB63568F92045F2798F2AA3F40FF306939E42A1E1731E"
        ), "AGENTS_LEGACY.md is immutable and must not change"