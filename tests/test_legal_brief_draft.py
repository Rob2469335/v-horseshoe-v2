"""Tests for the brief/motion checklist checker
(swarm_os/services/legal/brief_draft.py).

Research-grounded (Harvey Legal Agent Benchmark 2026): models pass ~90% of
individual rubric criteria but fail the conjunctive deliverable ~80% of the
time. The fix is a structure-first skeleton + a post-generation checker that
re-verifies every citation — not a stronger prompt. These tests pin the
deterministic checker: uncited assertions flagged, citations aligned against
the retrieved corpus, and a PASS/FAIL verdict.
"""
from __future__ import annotations

from swarm_os.services.legal.brief_draft import (
    draft_skeleton, check_brief, render_check, check_frap32, check_fidelity,
)


def test_draft_skeleton_has_all_required_sections():
    sk = draft_skeleton()
    sections = [s["section"] for s in sk]
    for required in ("Cover Page", "Jurisdictional Statement", "Statement of Issues",
                     "Statement of the Case", "Argument", "Conclusion",
                     "Certificate of Compliance", "Addendum"):
        assert required in sections, f"brief missing required section: {required}"


def test_draft_skeleton_issue_headers_become_argument_blocks():
    sk = draft_skeleton(["the loss calculation", "the restitution award"])
    arg_issues = [s["section"] for s in sk if s["section"].startswith("Argument Issue")]
    assert len(arg_issues) == 2
    assert "loss calculation" in arg_issues[0]


def test_check_brief_flags_uncited_assertion():
    """An Argument sentence asserting a rule WITHOUT a citation must be flagged —
    the 'models pass 90% of criteria but fail the deliverable' case."""
    argument = ("The defendant is entitled to a new trial because the loss "
                "calculation was clearly erroneous. See 945 F.3d 687.")
    check = check_brief(argument, retrieved_statutes=[], retrieved_cases=["945 F.3d 687"])
    assert check["uncited_count"] == 1
    assert check["uncited_assertions"][0]["sentence"].startswith("The defendant is entitled")
    assert not check["ok"]


def test_check_brief_passes_fully_cited_argument():
    argument = ("The rule of United States v. Moseley, 980 F.3d 9 requires a "
                "reasonable loss methodology. Here the methodology was crude.")
    check = check_brief(argument, retrieved_statutes=[], retrieved_cases=["980 F.3d 9"])
    # "Here the methodology was crude" is an application sentence without a cite
    # — the checker must flag it (application sentences need a record/authority).
    assert check["uncited_count"] == 1  # the "Here ..." application sentence
    assert not check["ok"]


def test_check_brief_aligns_case_citations_to_retrieved():
    """A cited case absent from the retrieved corpus AND the manifest is an
    unaligned citation (the M6 fabrication signal) — the checker must surface
    it in unaligned_cases."""
    argument = "The 999 F.4th 1 case controls."
    check = check_brief(argument, retrieved_statutes=[], retrieved_cases=["252 F.3d 238"])
    assert "999 F.4th 1" in check["unaligned_cases"]
    assert not check["ok"]


def test_check_brief_aligns_statutes_to_retrieved():
    argument = "The deposit rule in N.Y. RPA Law § 235-b applies."
    check = check_brief(argument, retrieved_statutes=["N.Y. RPA Law § 235-b"], retrieved_cases=[])
    assert check["unaligned_statutes"] == []
    assert check["ok"]


def test_render_check_readable():
    check = check_brief("The defendant is entitled to relief.", [], [])
    out = render_check(check)
    assert "FAIL" in out
    assert "uncited" in out.lower()


# --- FRAP 32 type-volume + certificate lint (Build 4) -------------------------

def test_frap32_principal_limit_is_13000():
    """FRAP 32(a)(7)(B)(i): a principal brief may contain no more than 13,000
    words. Pinned to the actual rule text (LII, fetched 2026-08) — the old
    14,000 was wrong by 1,000 words."""
    from swarm_os.services.legal.brief_draft import _FRAP32_WORD_LIMIT, _FRAP32_REPLY_WORD_LIMIT
    assert _FRAP32_WORD_LIMIT == 13000
    assert _FRAP32_REPLY_WORD_LIMIT == 6500  # half, per 32(a)(7)(B)(ii)


def test_frap32_under_limit_with_certificate_ok():
    text = ("Certificate of Compliance: This brief complies with FRAP 32(a)(7).\n" +
            "word " * 100)
    res = check_frap32(text)
    assert res["ok"] is True
    assert res["words"] <= res["limit"]
    assert res["has_certificate"] is True
    assert res["limit"] == 13000


def test_frap32_over_limit_fails():
    text = ("Certificate of Compliance.\n" + "word " * 15000)
    res = check_frap32(text)
    assert res["ok"] is False
    assert res["over"] is True
    assert res["remaining"] == 0


def test_frap32_reply_limit_half_volume():
    """A reply brief at 6,500 words is the limit (32(a)(7)(B)(ii)); over it
    fails. 6,500 is fine for a reply but the SAME text as a principal brief is
    well under — the reply limit is stricter."""
    text = "Certificate of Compliance.\n" + "word " * 6500
    res = check_frap32(text, reply=True)
    assert res["limit"] == 6500
    assert res["ok"] is False  # exactly at limit is over (> not >=), plus cert
    # 6,500 words is under the principal 13,000 limit.
    res_principal = check_frap32(text, reply=False)
    assert res_principal["limit"] == 13000


def test_frap32_missing_certificate_fails():
    res = check_frap32("short text no certificate")
    assert res["ok"] is False
    assert res["has_certificate"] is False


def test_frap32_accepts_word_count_dict():
    res = check_frap32({"text": "x", "word_count": 100})
    assert res["words"] == 100


# --- LegalCiteTrust fidelity pass (Build 5) ----------------------------------

def test_check_fidelity_supporting_citation():
    """A sentence whose cited source overlaps its substantive tokens is
    SUPPORTING — the citation actually backs the claim."""
    source = {"507|us|725": "The plain error standard requires a clear or obvious error affecting substantial rights."}
    res = check_fidelity(
        "The plain error standard requires a clear or obvious error affecting substantial rights, per 507 U.S. 725.",
        source)
    assert res["checked"] == 1
    assert res["rate"] == 1.0
    assert len(res["unsupporting"]) == 0


def test_check_fidelity_unsupporting_citation():
    """A sentence whose cited source shares almost no tokens is UNSUPPORTING —
    the 'cite exists but doesn't say what I claim' error LegalCiteTrust warns
    about."""
    source = {"507|us|725": "The standard requires clear error affecting substantial rights."}
    res = check_fidelity(
        "The defendant's flight from the scene proves consciousness of guilt, per 507 U.S. 725.",
        source)
    assert res["checked"] == 1
    assert res["rate"] == 0.0
    assert len(res["unsupporting"]) == 1


def test_check_fidelity_catches_fabricated_claim_by_case_name():
    """REGRESSION: a sentence that FABRICATES a holding about a REAL case by
    NAME ONLY (no reporter cite, so eyecite can't parse it) must be flagged
    unsupporting — the 'Moseley held that the defendant's flight proved
    consciousness of guilt' failure. The real Moseley source is about loss
    methodology, not flight. Before the name-resolution fix, this sentence
    had no parseable citation, was skipped, and the fabrication passed with
    rate 1.0."""
    source = {"980|f3d|9": (
        "Moseley, 980 F.3d 9: the district court's loss calculation must use a "
        "reasonable methodology; the court reviews the loss calculation for "
        "clear error."
    )}
    argument = (
        "The rule of United States v. Moseley, 980 F.3d 9 requires a reasonable "
        "methodology for the loss calculation. United States v. Moseley held that "
        "the defendant's flight from the scene proved consciousness of guilt, "
        "which is dispositive here."
    )
    res = check_fidelity(argument, source)
    assert res["checked"] == 2
    assert len(res["unsupporting"]) == 1
    assert "flight" in res["unsupporting"][0]["sentence"]
    assert res["rate"] == 0.5  # the real sentence supports; the fabricated one does not


def test_check_fidelity_supports_name_only_reference_when_accurate():
    """A name-only reference that ACCURATELY states what the case held must be
    SUPPORTING — the name resolution must not false-positive on honest
    short-form cites."""
    source = {"980|f3d|9": (
        "Moseley, 980 F.3d 9: the loss calculation must use a reasonable "
        "methodology; reviewed for clear error."
    )}
    argument = "Moseley requires a reasonable loss-calculation methodology."
    res = check_fidelity(argument, source)
    assert res["checked"] == 1
    assert res["rate"] == 1.0
    assert len(res["unsupporting"]) == 0


def test_check_fidelity_no_citation_sentence_skipped():
    res = check_fidelity("This is a purely narrative sentence.", {})
    assert res["checked"] == 0
    assert res["rate"] == 0.0
