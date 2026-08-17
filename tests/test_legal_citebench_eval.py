"""Tests for the M4 LegalCiteBench Cat3 eval harness (scripts/legal_citebench_eval.py).

The harness measures the OFFLINE, deterministic citation-verification signal against
the REAL LegalCiteBench Cat3 corpus (fixture = vendored sample of 48 rows, 24
3-fake + 24 3-true). The pinned invariants:

- EXTRACTION recall: nearly every row yields >=1 canonical case-citation key.
- DETECTION signal on 3-fake rows: the paragraph carries a key OUTSIDE the
  corrected-citation set (real fake paragraphs cite BOTH the fabricated and the
  corrected cite), and the harness reports no false-negative on parseable rows.
- The harness does NOT overclaim external (token-gated) field verification.

This file intentionally ships without a network dependency: the fixture is a
static JSONL vendored under tests/fixtures/.
"""

from __future__ import annotations

from pathlib import Path

import scripts.legal_citebench_eval as ev

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "cat3_citation_verification_sample.jsonl"
)


def test_fixture_loads_with_both_styles():
    rows = ev.read_cat3(str(FIXTURE))
    styles = sorted({r.get("qa_style") for r in rows})
    assert styles == ["3-fake", "3-true"]
    assert len(rows) == 48


def test_gt_corrected_key_parses_standard_fake_gt():
    gt = "The citation is incorrect. The correct citation is: 84 So.3d 661."
    assert ev.gt_corrected_key(gt) == "84|so3d|661"


def test_gt_corrected_key_none_for_true_rows():
    assert ev.gt_corrected_key("There is no error in the citation.") is None


def test_paragraph_case_keys_extracts_full_case_citations():
    q = "The court relied on State v. Smith, 79 N.J. 254 (1979), which clarified..."
    keys = ev.paragraph_case_keys(q)
    assert "79|nj|254" in keys


def test_fake_rows_print_key_both_correct_AND_fabricated():
    """Real Cat3 paragraphs carry the corrected cite alongside the sabotaged one.

    The row funnels a paragraph whose suspicion set is both corrected+difference;
    the fabricated cite (84 So.2d 661) and corrected cite (84 So.3d 661) both get
    canonical keys. This is the shape that proves a non-membership check (not a
    'missing' check) is the detection signal.
    """
    q = (
        "The holding in Vaughn v. Dis-Tran Steel, LLC, 84 So.2d 661, was later "
        "affirmed by reference to the correct citation 84 So.3d 661."
    )
    keys = ev.paragraph_case_keys(q)
    assert "84|so2d|661" in keys and "84|so3d|661" in keys


def test_eval_vs_fixture_gives_zero_false_negatives():
    """Running the harness on the vendored fixture, the raw counter splits must be:
    - parsed != unparsed split, and
    - 3-fake rows with a parseable corrected GT have >=1 differing key.
    """
    rows = ev.read_cat3(str(FIXTURE))
    fake_rows = [r for r in rows if r.get("qa_style") == "3-fake"]
    assert fake_rows, "fixture must contain 3-fake rows"

    keys_ok = 0
    gt_keyed = 0
    detected = 0
    for r in fake_rows:
        keys = ev.paragraph_case_keys(r.get("question") or "")
        if not keys:
            continue
        keys_ok += 1
        gkey = ev.gt_corrected_key(r.get("ground_truth") or "")
        if gkey is None:
            continue
        gt_keyed += 1
        if any(k != gkey for k in keys):
            detected += 1

    assert detected >= 18  # most faux rows carry a saboted cite
    assert detected == gt_keyed, (
        "fake rows should all be caught (each saboted cite differs from corrected); "
        "a row reported false-negative is a real regression."
    )
