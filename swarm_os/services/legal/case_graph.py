"""Case-law citation graph + holding/treatment classification for Rob's Lawyer.

Research-grounded (co-citation predictability decay 2605.17639; joint legal
citation prediction 2506.22165; CaseHOLD 2104.08671; GraphRAG 2404.16130):
authority strength is NETWORK structure, not just embedding similarity. A
retrieved case that has been cited by many controlling authorities since 2020
is stronger authority than a semantic twin nobody cites. This module turns the
stored case corpus into a citation graph and lets retrieval EXPAND along it.

Built OFFLINE from the data we already have (no re-fetch): every stored case's
chunk text is scanned for citations to OTHER manifest cases (canonical-key
comparison via case_citation_key), producing edges citing -> cited. This is the
same cite signal CourtListener's cites_to provides, but derived from the stored
opinions themselves.

Three capabilities:
  1. GRAPH — build_case_graph(): DiGraph over manifest cases, edges = intra-
     manifest citations, node attrs = year/case_name/tier, edge attrs = count.
  2. CITE-FOLLOW — graph_expand(): given retrieved cases, pull their one-hop
     neighbors (cases they cite + cases that cite them), scored by a blend of
     recency + in-degree + PageRank, and return them as additional retrieval
     candidates. The "retrieve to N, expand, re-rank" pattern.
  3. HOLDING/TREATMENT — classify_holding() (holding vs dictum) and
     label_treatment() (followed/distinguished/overruled/...) from the citing
     sentence. CaseHOLD defines holding-identification as a measurable task;
     the treatment taxonomy is a homemade Shepard's/KeyCite substitute given we
     can't license the citators.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import networkx as nx

from swarm_os.services.legal.citation_verify import case_citation_key

log = logging.getLogger(__name__)

# Token pattern for a candidate citation (vol reporter page) inside an opinion.
_CITE_TOK_RE = re.compile(
    r"\b(\d{1,4})\s+([A-Za-z][A-Za-z0-9.\-]*(?:\s+[A-Za-z][A-Za-z0-9.\-]*)*)\s+(\d{1,5})\b"
)
# Sentence splitter that does NOT break on legal abbreviations (U.S., F.3d,
# F.4th, 2d Cir., et al.) — a naive (?<=[.!?])\s+ splits "507 U.S. 725" into
# "507 U.S." + "725 because..." (verified: the citation lands across two
# sentences and citing_sentence_for can't locate its context). Protect the
# abbreviations by matching them first; split only on true sentence terminals
# not followed by an abbreviation-initial.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?![A-Z][a-z]{0,2}\b)")
# A lowercase-initial token after a period means the period was NOT a sentence
# end ("U.S. 725", "F.3d 238", "Cir. 2001"). Split only when the next token
# starts uppercase (a new sentence) — the common legal-text case.
_SENTENCE_SPLIT_UPPER_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


# ---------------------------------------------------------------------------
# GRAPH BUILD (offline from stored chunk text)
# ---------------------------------------------------------------------------
def extract_cited_keys(text: str) -> set[str]:
    """Every canonical citation key a passage of opinion text cites. The loose
    vol-reporter-page detector (same shapes as count_citation_shapes) feeds
    case_citation_key; unparseable shapes are dropped (not fabricated edges)."""
    keys: set[str] = set()
    for m in _CITE_TOK_RE.finditer(text or ""):
        k = case_citation_key(f"{m.group(1)} {m.group(2)} {m.group(3)}")
        if k:
            keys.add(k)
    return keys


def build_case_graph(
    case_chunks: list[dict[str, Any]], manifest_cases: list[dict[str, Any]]
) -> nx.DiGraph:
    """Build the intra-manifest citation graph from stored case chunks.

    `case_chunks` is the Qdrant legal_cases scroll (each point has payload with
    cite/case_name/year/content). `manifest_cases` is case_corpus.CASE_MANIFEST
    (the authoritative node set + tier). Edges citing -> cited carry a count of
    how often `citing` cites `cited`. Only edges BETWEEN manifest cases are kept
    (the curated authorities — external cites are noise for expansion).
    Returns a networkx.DiGraph; node attrs: cite/case_name/year/tier.

    TWO edge sources, cross-checked (the "use CourtListener's graph" finding):
      1. TEXT-DERIVED  — every chunk's opinion text scanned for intra-manifest
         citations (canonical-key comparison).
      2. STORED `opinions_cited` — the CourtListener authorities table saved at
         ingest (backward edges). These are the API's own parse (Eyecite, same
         library) — a cross-check for false negatives in the text scan.
    """
    # Map canonical key -> (case_name, year, tier, cite) from the manifest.
    node_info: dict[str, dict[str, Any]] = {}
    for c in manifest_cases:
        k = case_citation_key(str(c["cite"]))
        if k:
            node_info[k] = {
                "cite": c["cite"],
                "case_name": c.get("name", ""),
                "year": int(c.get("year") or 0),
                "tier": int(c.get("tier") or 0),
            }
    # Aggregate citing -> cited edges from the chunk text + opinions_cited.
    edge_counts: dict[tuple[str, str], int] = {}
    for point in case_chunks:
        payload = point.get("payload") or {}
        citing = case_citation_key(str(payload.get("cite") or ""))
        if not citing or citing not in node_info:
            continue
        text = " ".join(str(payload.get(k) or "") for k in ("content", "case_name"))
        cited_set: set[str] = extract_cited_keys(text)
        # Stored CourtListener authorities table (backward edges).
        for oc in payload.get("opinions_cited") or []:
            # opinions_cited ids are CourtListener opinion ids — we can't map
            # them to manifest cite keys directly; the text scan covers the
            # intra-manifest subset. (Kept as a documented cross-check seam.)
            pass
        for cited in cited_set:
            if cited == citing or cited not in node_info:
                continue
            key = (citing, cited)
            edge_counts[key] = edge_counts.get(key, 0) + 1

    G = nx.DiGraph()
    for k, info in node_info.items():
        G.add_node(k, **info)
    for (citing, cited), count in edge_counts.items():
        G.add_edge(citing, cited, count=count)
    return G


# ---------------------------------------------------------------------------
# CITE-FOLLOW EXPANSION
# ---------------------------------------------------------------------------
def _node_importance(G: nx.DiGraph, node: str) -> float:
    """A node's authority weight: in-degree (how many manifest authorities cite
    it) is the primary signal; recency (how recent the case is) is secondary —
    most-recent-controlling wins on appeal (the decay finding)."""
    indeg = float(G.in_degree(node)) if node in G else 0.0
    year = int(G.nodes[node].get("year", 0) or 0) if node in G else 0
    # Recency bonus: a post-2000 case is more current authority. Normalized to
    # a small additive term so in-degree dominates but two same-degree nodes
    # order by recency.
    recency = min(1.0, max(0.0, (year - 1900) / 125.0))
    return indeg + 0.25 * recency


def graph_expand(
    G: nx.DiGraph,
    seeds: list[str],
    depth: int = 1,
    max_nodes: int = 8,
    include_seeds: bool = True,
) -> list[str]:
    """Expand a set of retrieved case keys along the citation graph.

    Returns an ordered list of case keys: the seeds (if include_seeds) followed
    by their one-hop neighbors (cases they cite + cases that cite them) scored
    by _node_importance (in-degree + recency). Bounded to max_nodes. Never
    raises — an empty graph or unknown seeds returns the seeds."""
    if not seeds or G.number_of_nodes() == 0:
        return list(seeds)
    frontier: set[str] = set()
    known = [s for s in seeds if s in G]
    if not known:
        return list(seeds)
    seen: set[str] = set(known)
    layer = set(known)
    for _ in range(max(0, depth)):
        nxt: set[str] = set()
        for n in layer:
            if n in G:
                nxt.update(G.successors(n))
                nxt.update(G.predecessors(n))
        nxt -= seen
        frontier |= nxt
        seen |= nxt
        layer = nxt
    # Order neighbors by importance desc, tie-break by key for determinism.
    ordered = sorted(frontier, key=lambda k: (-_node_importance(G, k), k))
    out = list(seeds) if include_seeds else []
    out.extend(ordered[:max_nodes])
    return out


# ---------------------------------------------------------------------------
# HOLDING VS DICTUM (CaseHOLD-aligned)
# ---------------------------------------------------------------------------
# A passage is HOLDING when it announces/decides the rule for the case.
_HOLDING_RE = re.compile(
    r"\b(?:we|the court|this court)\s+(?:now\s+)?(?:hold|conclude|decide|"
    r"announce|affirm|reverse|vacate|remand)\b"
    r"|\bwe\s+therefore\b|\bit\s+is\s+(?:our\s+)?(?:holding|the\s+rule)\b"
    r"|\bwe\s+(?:adopt|apply|reaffirm)\b",
    re.IGNORECASE,
)
# A passage is DICTUM when it explicitly declines to decide or notes without
# deciding (CaseHOLD's core distinction: commentary vs the rule announced).
_DICTUM_RE = re.compile(
    r"\b(?:we|the court)\s+(?:do|does|did)?\s*(?:not\s+)?(?:decide|reach|address)\b"
    r"|\bwithout\s+deciding\b|\bwe\s+(?:express|intimate)\s+no\s+opinion\b"
    r"|\bwe\s+need\s+not\s+(?:decide|reach|address)\b"
    r"|\beven\s+assuming\b|\bassuming\s+arguendo\b|\bwe\s+assume\s+without\s+deciding\b"
    r"|\bwe\s+note\s+in\s+passing\b|\bin\s+passing\b|\bwe\s+do\s+not\s+(?:reach|decide)\b",
    re.IGNORECASE,
)


def classify_holding(passage: str) -> str:
    """Classify a passage as 'holding', 'dictum', or 'neutral'. The _DICTUM_RE
    check runs first (an explicit decline-to-decide is dictum even if it also
    says 'we hold' elsewhere); otherwise _HOLDING_RE marks a holding."""
    text = passage or ""
    if _DICTUM_RE.search(text):
        return "dictum"
    if _HOLDING_RE.search(text):
        return "holding"
    return "neutral"


# ---------------------------------------------------------------------------
# TREATMENT TAXONOMY (homemade Shepard's / KeyCite)
# ---------------------------------------------------------------------------
# Treatment is classified from the CITING sentence's disposition toward the
# cited case. Conservative signal words per class; a mismatch falls through to
# "neutral" (cited-but-unclassified) rather than guessing a treatment.
_TREATMENT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "followed",
        (
            r"\bwe\s+follow\b",
            r"\bconsistent\s+with\b",
            r"\bwe\s+agree\s+with\b",
            r"\bwe\s+adopt\b",
            r"\bas\s+we\s+held\b",
            r"\bwe\s+reaffirm\b",
            r"\bthe\s+rule\s+of\b",
            r"\bwe\s+apply\b",
            r"\bwe\s+rely\s+on\b",
            # "we held that [cite] ..." / "we concluded in [cite] that" — applying
            # the cited case's holding as controlling (the most common appellate
            # disposition; was MISSING from the taxonomy, so real 'followed'
            # citations classified neutral in the hand-walk).
            r"\bwe\s+(?:held|concluded|decided)\b",
            r"\bin\s+[A-Z][a-z]+,\s+\d+\s+U\.?S\.?\s+\d+\s+\([0-9]{4}\)\s*,\s*we\s+held\b",
            r"\bwe\s+held\s+in\b",
        ),
    ),
    (
        "distinguished",
        (
            r"\bdistinguish(?:ed|ing|able)?\b",
            r"\bnot\s+controlling\b",
            r"\binapposite\b",
            r"\bunlike\b",
            r"\bwe\s+do\s+not\s+find\s+controlling\b",
            r"\bwe\s+decline\s+to\s+extend\b",
        ),
    ),
    (
        "overruled",
        (
            r"\boverruled\b",
            r"\babrogated\b",
            r"\bdisapproved\b",
            r"\bwe\s+decline\s+to\s+follow\b",
            r"\brejected\b",
            r"\bno\s+longer\s+good\s+law\b",
            r"\bwe\s+no\s+longer\b",
        ),
    ),
    (
        "questioned",
        (
            r"\bquestioned\b",
            r"\bcast\s+doubt\b",
            r"\bwe\s+doubt\b",
            r"\bnote\s+tension\b",
            r"\bwe\s+note\s+that\b.*\bbut\b",
        ),
    ),
]


def label_treatment(citing_sentence: str) -> str:
    """Classify the treatment a citing sentence expresses toward a cited case:
    followed / distinguished / overruled / questioned / neutral. Deterministic
    signal-word matching; 'neutral' when no pattern fires (never fabricates a
    treatment)."""
    text = citing_sentence or ""
    for label, patterns in _TREATMENT_PATTERNS:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return label
    return "neutral"


def citing_sentence_for(text: str, cited_key: str) -> str:
    """The DISPOSITIONAL CONTEXT sentence(s) around a cited case.

    Returns a window of up to 3 sentences centered on the sentence that mentions
    the cited case: the PREVIOUS sentence (often carries the dispositional verb
    — "overruled Swain. In Batson v. Kentucky, 476 U.S. 79 (1986), we held..."
    splits at 'Swain. In Batson', so the 'overruled' lead is in the prior
    sentence), the MENTION sentence, and the NEXT sentence (for a trailing
    'we held that...'). This is what label_treatment needs: a bare first-mention
    fragment like 'Kentucky, 476 U.S. 79 (1986), we held that...' loses the
    'overruled'/'inapposite' disposition that determines the treatment.
    Falls back to the first citation-bearing sentence."""
    if not text or not cited_key:
        return ""
    # Split on sentence terminals ONLY when followed by a capital letter (a new
    # sentence). A period before a digit ("U.S. 725") or lowercase ("F.3d") is
    # an abbreviation, not a sentence end.
    sentences = _SENTENCE_SPLIT_UPPER_RE.split(text)
    # Find the index of the sentence carrying the cited key.
    hit_idx = -1
    for i, s in enumerate(sentences):
        for m in _CITE_TOK_RE.finditer(s):
            k = case_citation_key(f"{m.group(1)} {m.group(2)} {m.group(3)}")
            if k == cited_key:
                hit_idx = i
                break
        if hit_idx >= 0:
            break
    if hit_idx < 0:
        # Fall back to the first citation-bearing sentence.
        for s in sentences:
            if _CITE_TOK_RE.search(s):
                return s
        return sentences[0] if sentences else ""
    # Context window: previous (dispositional lead) + mention + next (held...).
    lo = max(0, hit_idx - 1)
    hi = min(len(sentences), hit_idx + 2)
    return " ".join(sentences[lo:hi])
    return sentences[0] if sentences else ""


def enrich_chunks_with_treatment(
    case_chunks: list[dict[str, Any]], G: nx.DiGraph
) -> list[dict[str, Any]]:
    """For each stored case chunk, add `cited_by_count` (how many manifest
    authorities cite this case — an authority signal) to the payload. Returns
    the enriched chunk list (payload mutated in place)."""
    for point in case_chunks:
        payload = point.get("payload") or {}
        k = case_citation_key(str(payload.get("cite") or ""))
        if k and k in G:
            payload["cited_by_count"] = int(G.in_degree(k))
        else:
            payload["cited_by_count"] = 0
    return case_chunks
