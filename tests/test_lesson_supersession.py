"""Step 2 — deterministic (subject, relation, object) supersession.

The premise these tests encode: similarity CANNOT distinguish a contradicted
lesson from a duplicated one (AUROC 0.59, near chance — arXiv:2606.26511), and
outdated instruction is worse than none because it produces confident errors
instead of abstention (HoH, ACL 2025). So the supersession decision must be a
pure function of the lesson text, not of embeddings.

No LLM, no embedding, no network, no store.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from swarm_os.services.lesson_supersession import (  # noqa: E402
    parse_sro,
    supersedes,
)


class TestParsing:
    def test_governed_condition_action_form(self):
        """`<condition>: <action>` splits at the colon."""
        sro = parse_sro("no-edit: use pathlib instead of os.path")
        assert sro.subject == "no edit"
        assert sro.complete

    def test_polarity_is_tracked_separately_from_value(self):
        """Polarity is captured, and stripped from the value.

        `use the write tool` and `never use the write tool` must land in the
        SAME slot with the SAME value, differing only in polarity — otherwise
        supersession cannot see them as mutually exclusive.
        """
        pos = parse_sro("no-edit: use the write tool")
        neg = parse_sro("no-edit: never use the write tool")
        assert pos.negated is False
        assert neg.negated is True
        assert pos.key == neg.key  # same slot
        assert pos.object == neg.object  # same value

    def test_relation_is_the_directive_verb(self):
        sro = parse_sro("call-loop: use ripgrep for searching")
        assert sro.relation == "use"
        assert "ripgrep" in sro.object
        assert "searching" in sro.object

    def test_leading_function_words_stripped_from_object(self):
        sro = parse_sro("turn-budget: always read the file before editing")
        assert sro.object == "file editing"

    def test_unparseable_is_incomplete_not_guessed(self):
        """No colon → no subject → incomplete triple → no verdict."""
        sro = parse_sro("just some free text")
        assert not sro.complete
        assert sro.subject == ""

    def test_empty_input_is_safe(self):
        assert not parse_sro("").complete
        assert not parse_sro(None).complete


class TestSupersessionVerdicts:
    def test_negated_replaces_affirmative_regardless_of_age(self):
        """Polarity correction wins even when it is the OLDER lesson.

        This is the dangerous stale case — an affirmative instruction that
        actively contradicts a corrective one. Age must not decide it.
        """
        affirmative = "no-edit: use the write tool"
        corrective = "no-edit: never use the write tool"
        assert supersedes(corrective, affirmative, candidate_newer=False) is True
        assert supersedes(affirmative, corrective, candidate_newer=True) is True

    def test_replaced_prescription_supersedes(self):
        """Same (subject, relation), different object → newer wins."""
        old = "call-loop: use grep for searching"
        new = "call-loop: use ripgrep for searching"
        assert supersedes(new, old, candidate_newer=True) is True

    def test_identical_rule_is_duplicate_not_supersession(self):
        """Exact duplicate is dedupe's job, not supersession's."""
        rule = "no-edit: use pathlib"
        assert supersedes(rule, rule, candidate_newer=True) is False

    def test_different_subject_never_supersedes(self):
        """Different slots are independent — no cross-contamination."""
        assert (
            supersedes(
                "turn-budget: use ripgrep",
                "no-edit: use ripgrep",
                candidate_newer=True,
            )
            is False
        )

    def test_older_candidate_never_supersedes_same_polarity(self):
        """Within one polarity, age is the caller's governed input.

        Age only decides a REPLACED prescription. Opposite polarity is decided
        by correction semantics instead (see the polarity test above).
        """
        assert (
            supersedes(
                "call-loop: use ripgrep for searching",
                "call-loop: use grep for searching",
                candidate_newer=False,
            )
            is False
        )

    def test_unparseable_incumbent_is_left_alone(self):
        """Fail-safe: never retire a lesson we cannot reason about."""
        assert (
            supersedes(
                "no-edit: use pathlib", "some unparseable free text", candidate_newer=True
            )
            is False
        )

    def test_unparseable_candidate_is_left_alone(self):
        assert (
            supersedes(
                "free text", "no-edit: use pathlib", candidate_newer=True
            )
            is False
        )


class TestDeterminism:
    def test_repeated_calls_agree(self):
        """No clock, no randomness, no model — same input, same verdict."""
        old = "no-edit: use the write tool"
        new = "no-edit: never use the write tool"
        verdicts = {supersedes(new, old, candidate_newer=True) for _ in range(50)}
        assert verdicts == {True}

    def test_order_independent_parse(self):
        a = parse_sro("no-edit: use pathlib")
        b = parse_sro("no-edit: use pathlib")
        assert a == b

    def test_whitespace_and_case_insensitive(self):
        assert parse_sro("  No-Edit:  Use Pathlib ") == parse_sro("no-edit: use pathlib")
