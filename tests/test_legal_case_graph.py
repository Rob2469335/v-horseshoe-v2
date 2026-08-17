"""Tests for the case-law citation graph + holding/treatment classification
(swarm_os/services/legal/case_graph.py).

Research-grounded (co-citation decay 2605.17639 / CaseHOLD 2104.08671): authority
strength is network structure, not just similarity. These tests pin:
  - the offline graph build from stored chunk text (canonical-key edges)
  - cite-follow expansion (one-hop neighbors, in-degree + recency ordered)
  - holding-vs-dictum classification (CaseHOLD-aligned)
  - the treatment taxonomy (followed/distinguished/overruled/questioned/neutral)
"""

from __future__ import annotations

from swarm_os.services.legal.case_graph import (
    extract_cited_keys,
    build_case_graph,
    graph_expand,
    classify_holding,
    label_treatment,
    citing_sentence_for,
    enrich_chunks_with_treatment,
)


def _chunk(cite, name, year, content):
    return {
        "id": cite,
        "payload": {
            "cite": cite,
            "case_name": name,
            "year": year,
            "content": content,
        },
    }


MANIFEST = [
    {"cite": "507 U.S. 725", "name": "Olano", "year": 1993, "tier": 1},
    {"cite": "252 F.3d 238", "name": "Simeonov", "year": 2001, "tier": 1},
    {"cite": "669 F.3d 112", "name": "Hsu", "year": 2012, "tier": 1},
    {"cite": "476 U.S. 79", "name": "Batson", "year": 1986, "tier": 4},
]


def test_extract_cited_keys():
    keys = extract_cited_keys("We follow the rule of 507 U.S. 725 and 252 F.3d 238.")
    assert "507|us|725" in keys
    assert "252|f3d|238" in keys
    assert "999|f3d|999" not in keys  # not present


def test_build_case_graph_edges_only_between_manifest():
    chunks = [
        _chunk(
            "507 U.S. 725",
            "Olano",
            1993,
            "The plain-error rule of United States v. Olano. See also 252 F.3d 238.",
        ),
        _chunk(
            "252 F.3d 238",
            "Simeonov",
            2001,
            "We follow the rule of 507 U.S. 725. 669 F.3d 112 agrees.",
        ),
        _chunk(
            "669 F.3d 112",
            "Hsu",
            2012,
            "We reaffirm 252 F.3d 238. The 999 F.3d 999 case is irrelevant.",
        ),
    ]
    G = build_case_graph(chunks, MANIFEST)
    assert set(G.nodes()) == {"507|us|725", "252|f3d|238", "669|f3d|112", "476|us|79"}
    # Olano chunk cites Simeonov; Simeonov chunk cites Olano + Hsu; Hsu cites Simeonov.
    assert G.has_edge("507|us|725", "252|f3d|238")
    assert G.has_edge("252|f3d|238", "507|us|725")
    assert G.has_edge("252|f3d|238", "669|f3d|112")
    assert G.has_edge("669|f3d|112", "252|f3d|238")
    # External cite 999 F.3d 999 must NOT create a node or edge.
    assert "999|f3d|999" not in G.nodes()
    # Batson (manifest but uncited) is still a node.
    assert G.has_node("476|us|79")
    # Edge counts aggregate.
    assert G.edges["507|us|725", "252|f3d|238"]["count"] >= 1


def test_build_case_graph_empty_chunks_is_empty_graph():
    G = build_case_graph([], MANIFEST)
    assert G.number_of_nodes() == len(MANIFEST)
    assert G.number_of_edges() == 0


def test_graph_expand_returns_seed_and_one_hop_by_importance():
    chunks = [
        _chunk("507 U.S. 725", "Olano", 1993, "See 252 F.3d 238 and 669 F.3d 112."),
        _chunk("252 F.3d 238", "Simeonov", 2001, "See 507 U.S. 725."),
        _chunk("669 F.3d 112", "Hsu", 2012, "See 507 U.S. 725."),
    ]
    G = build_case_graph(chunks, MANIFEST)
    # Expand from Olano: one-hop neighbors = Simeonov + Hsu (both cite Olano).
    expanded = graph_expand(G, ["507|us|725"], depth=1, max_nodes=5)
    assert expanded[0] == "507|us|725"
    assert "252|f3d|238" in expanded
    assert "669|f3d|112" in expanded


def test_graph_expand_orders_by_in_degree():
    """Hsu (cited by 2 authorities) must outrank Simeonov (cited by 1) when
    expanding — in-degree is the authority signal (the decay paper: hub cases
    resist decay)."""
    chunks = [
        _chunk("507 U.S. 725", "Olano", 1993, "See 252 F.3d 238 and 669 F.3d 112."),
        _chunk("252 F.3d 238", "Simeonov", 2001, "See 669 F.3d 112."),
        _chunk("669 F.3d 112", "Hsu", 2012, ""),
    ]
    G = build_case_graph(chunks, MANIFEST)
    # From Olano: neighbors are Simeonov (in-degree 1) and Hsu (in-degree 2).
    expanded = graph_expand(
        G, ["507|us|725"], depth=1, max_nodes=3, include_seeds=False
    )
    # Hsu is cited by Olano AND Simeonov -> in-degree 2 > Simeonov's in-degree 1.
    assert expanded[0] == "669|f3d|112"
    assert "252|f3d|238" in expanded


def test_graph_expand_safe_on_empty_and_unknown():
    G = build_case_graph([], MANIFEST)
    assert graph_expand(G, []) == []
    assert graph_expand(G, ["999|f3d|999"]) == ["999|f3d|999"]


def test_classify_holding_vs_dictum():
    assert classify_holding("We hold that the rule requires notice.") == "holding"
    assert (
        classify_holding("It is our holding that the defendant is entitled to relief.")
        == "holding"
    )
    assert classify_holding("We do not reach the constitutional question.") == "dictum"
    assert (
        classify_holding("We need not decide whether the statute applies.") == "dictum"
    )
    assert (
        classify_holding("Assuming arguendo that the claim has merit, we reject it.")
        == "dictum"
    )
    assert classify_holding("The facts of this case are straightforward.") == "neutral"


def test_label_treatment_taxonomy():
    assert label_treatment("We follow the rule of 507 U.S. 725.") == "followed"
    assert label_treatment("This case is consistent with 252 F.3d 238.") == "followed"
    assert (
        label_treatment("The present case is distinguishable from Olano.")
        == "distinguished"
    )
    assert (
        label_treatment("We decline to extend 669 F.3d 112 to these facts.")
        == "distinguished"
    )
    assert (
        label_treatment("Simeonov was overruled by the Supreme Court.") == "overruled"
    )
    assert label_treatment("Hsu is no longer good law.") == "overruled"
    assert (
        label_treatment("The Court questioned the reasoning of Batson.") == "questioned"
    )
    assert label_treatment("The parties cite these cases.") == "neutral"


def test_label_treatment_real_appellate_shapes():
    """REGRESSION (from the citator hand-walk): the two most common REAL
    appellate dispositions were classifying neutral. 'we held that' after a
    citation is FOLLOWED (applying the cited case as controlling); 'inapposite'
    is DISTINGUISHED. Both are pinned against the actual stored-corpus shapes."""
    assert (
        label_treatment(
            "In Batson v. Kentucky, 476 U.S. 79 (1986), we held that a defendant "
            "may make a prima facie showing of purposeful racial discrimination."
        )
        == "followed"
    )
    assert (
        label_treatment(
            "Batson is inapposite here because the strike was not race-based."
        )
        == "distinguished"
    )


def test_citing_sentence_for_returns_dispositional_window():
    """REGRESSION (from the citator hand-walk): citing_sentence_for must return
    the DISPOSITIONAL CONTEXT (prior 'overruled Swain' lead + mention + trailing
    'we held'), not a bare first-mention fragment — a fragment loses the verb
    that determines the treatment. The real shape 'overruled Swain. In Batson
    v. Kentucky, 476 U.S. 79 (1986), we held that...' splits at 'Swain. In',
    so the window must span the prior sentence."""
    real = (
        "Five years ago we revisited the issue, and overruled Swain. In Batson v. "
        "Kentucky, 476 U.S. 79 (1986), we held that a defendant may make a prima "
        "facie showing of purposeful racial discrimination in selection of the venire."
    )
    context = citing_sentence_for(real, "476|us|79")
    assert "overruled" in context or "we held" in context, (
        "the dispositional verb must be in the context window"
    )
    # And the window feeds label_treatment correctly.
    assert label_treatment(context) == "followed"


def test_citing_sentence_for_inapposite_window():
    real = (
        "The defendant argues Batson v. Kentucky, 476 U.S. 79 (1986) requires reversal. "
        "We disagree — Batson is inapposite here because the strike was not race-based."
    )
    context = citing_sentence_for(real, "476|us|79")
    assert "inapposite" in context
    assert label_treatment(context) == "distinguished"


def test_citing_sentence_for_locates_context():
    text = (
        "The rule is settled. We follow 507 U.S. 725 because it controls. "
        "The other cases are irrelevant."
    )
    sent = citing_sentence_for(text, "507|us|725")
    assert "follow" in sent
    assert "507 U.S. 725" in sent


def test_enrich_chunks_with_treatment_adds_cited_by_count():
    chunks = [
        _chunk("507 U.S. 725", "Olano", 1993, "See 252 F.3d 238."),
        _chunk("252 F.3d 238", "Simeonov", 2001, ""),
    ]
    G = build_case_graph(chunks, MANIFEST)
    enriched = enrich_chunks_with_treatment(list(chunks), G)
    by_cite = {p["payload"]["cite"]: p["payload"]["cited_by_count"] for p in enriched}
    assert by_cite["252 F.3d 238"] == 1  # cited by Olano
    assert by_cite["507 U.S. 725"] == 0  # not cited by any manifest case
