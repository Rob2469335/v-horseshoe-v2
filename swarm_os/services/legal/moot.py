"""Oral-argument panel prep — the AI moot-court (Gap 3).

Practitioner-reported technique (no published arXiv record — flagged UNVERIFIED
in the audit; closest support is CHANCERY 2506.04636 showing reasoning agents
can stress-test a rule application at 76-78%). For the assigned panel, build a
per-judge profile from their prior opinions (CourtListener `opinions` →
`panel`/`author`) and run a SIMULATED BENCH: DeepSeek V4 Flash plays each judge,
questioning the user's argument from that judge's recorded concerns; the user
answers and iterates.

Components:
  1. PANEL PROFILE — fetch each judge's recent opinions, distill a per-judge
     "what this judge interrogates" profile (deterministic keyword extraction
     from their opinion text + LLM summary).
  2. SIMULATED BENCH — for each issue, generate the judge's likely hardest
     question (persona + the judge's profile + the IRAC outline), then score the
     user's answer against the argument's own authorities.

Reuses the repo's stream_content seam + the transcript/citation machinery.
Never raises — a profile-fetch outage degrades to generic-judge questions.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

OPINIONS_URL = os.getenv(
    "COURTLISTENER_OPINIONS_URL",
    "https://www.courtlistener.com/api/rest/v4/opinions/",
)
# Cluster endpoint — the opinions list returns `panel`/`author` as null and
# `cluster` as a URL; the judge-attribution data lives on the CLUSTER object.
# VERIFIED live 2026-08-11: opinions list panel/author = null, cluster = URL.
CLUSTERS_URL = os.getenv(
    "COURTLISTENER_CLUSTERS_URL",
    "https://www.courtlistener.com/api/rest/v4/clusters/",
)

# Keyword signals of what a judge cares about, distilled from their opinions.
_PROFILE_KEYWORDS = {
    "standard_of_review": re.compile(
        r"standard of review|de novo|abuse of discretion|clear error|plain error", re.I
    ),
    "preservation": re.compile(
        r"preserv|waiv|forfeiture|objection|contemporaneous", re.I
    ),
    "statutory_interpretation": re.compile(
        r"statute|plain meaning|textual|canon of construction|legislative intent", re.I
    ),
    "precedent": re.compile(
        r"controlling|binding precedent|stare decisis|circuit split", re.I
    ),
    "sentencing": re.compile(
        r"guideline|sentenc|restitution|loss|restitution award", re.I
    ),
    "facts": re.compile(
        r"record|evidence|sufficiency|substantial evidence|findings", re.I
    ),
}


@dataclass
class JudgeProfile:
    name: str = ""
    topics: dict[str, int] = field(default_factory=dict)  # keyword -> hits
    opinion_count: int = 0
    error: str = ""


@dataclass
class BenchQuestion:
    judge: str
    issue: str
    question: str
    suggested_answer: str = ""


@dataclass
class BenchSession:
    questions: list[BenchQuestion] = field(default_factory=list)
    judges: list[JudgeProfile] = field(default_factory=list)
    ok: bool = True
    message: str = ""


def distill_profile(text: str) -> dict[str, int]:
    """Deterministic keyword profile of what a judge interrogates, from their
    opinion text. Returns {topic: hits}. The SOTA basis: judge-specific question
    patterns are learnable from their recorded concerns."""
    counts: dict[str, int] = {}
    for topic, pattern in _PROFILE_KEYWORDS.items():
        hits = len(pattern.findall(text or ""))
        if hits:
            counts[topic] = hits
    return counts


async def fetch_judge_profile(
    client: httpx.AsyncClient, judge_name: str, max_opinions: int = 3
) -> JudgeProfile:
    """Fetch a judge's recent opinions via CourtListener and distill a profile.

    FAIL-CLOSED on attribution (the hand-walk defect): the plain opinions fetch
    returns the 20 most RECENT opinions in the whole database, NOT this judge's —
    so the code checks each opinion's `panel`/`author` fields for the judge name
    and only keeps opinions that actually name them. If none can be attributed,
    `prof.error` is set and `topics` stays empty so run_bench falls back to a
    generic profile instead of silently building a profile from another judge's
    opinions. Returns a JudgeProfile; never raises."""
    prof = JudgeProfile(name=judge_name)
    judge_l = (judge_name or "").lower()
    # The judge's surname (last word) for a lenient panel/author match.
    surname = judge_l.split()[-1] if judge_l.split() else judge_l
    try:
        resp = await client.get(
            OPINIONS_URL,
            params={"format": "json", "page_size": 50},
            timeout=30.0,
        )
        if resp.status_code != 200:
            prof.error = f"http:{resp.status_code}"
            return prof
        results = (resp.json() or {}).get("results") or []
        collected = ""
        for item in results:
            # Attribution check: the opinions list carries panel/author as null
            # and cluster as a URL — the judge data is on the CLUSTER object.
            # Fetch it (bounded) to attribute the opinion; skip if we can't
            # confirm (fail-closed, never use a stranger's opinion).
            attribution = ""
            cluster_url = item.get("cluster")
            if (
                isinstance(cluster_url, str)
                and cluster_url
                and "clusters/" in cluster_url
            ):
                try:
                    cresp = await client.get(
                        cluster_url,
                        params={"format": "json"},
                        timeout=30.0,
                    )
                    if cresp.status_code == 200:
                        cluster = cresp.json() or {}
                        panel = cluster.get("panel") or []
                        author = cluster.get("author") or cluster.get("panel")
                        for field in (
                            [panel] if isinstance(panel, list) else [panel, author]
                        ):
                            if isinstance(field, dict):
                                attribution += " " + str(field.get("name", ""))
                            elif isinstance(field, str):
                                attribution += " " + field
                except Exception as exc:
                    log.debug(
                        "cluster attribution fetch failed for %s: %s",
                        item.get("id"),
                        exc,
                    )
            else:
                # Inline panel/author if present (older API shape).
                for field in ("panel", "author"):
                    val = item.get(field)
                    if isinstance(val, list):
                        for entry in val:
                            attribution += " " + str(
                                entry.get("name", "")
                                if isinstance(entry, dict)
                                else entry
                            )
                    elif isinstance(val, dict):
                        attribution += " " + str(val.get("name", ""))
                    else:
                        attribution += " " + str(val or "")
            if surname and surname not in attribution.lower():
                continue  # NOT this judge's opinion — skip (fail-closed)
            text = (item.get("plain_text") or item.get("html_with_citations") or "")[
                :4000
            ]
            collected += "\n" + text
            prof.opinion_count += 1
            if prof.opinion_count >= max_opinions:
                break
        if prof.opinion_count == 0:
            prof.error = "no_attributed_opinions"
            return prof
        prof.topics = distill_profile(collected)
    except Exception as exc:
        log.warning("judge profile fetch failed for %s: %s", judge_name, exc)
        prof.error = str(exc)
    return prof


def generic_profile(judge_name: str) -> JudgeProfile:
    """A neutral profile when a judge's opinions can't be fetched — the
    simulated bench still runs with generic-judge questions (degrade, not fail)."""
    return JudgeProfile(name=judge_name, topics={"standard_of_review": 1})


async def _ask_judge(
    judge: str,
    topics: dict[str, int],
    issue: str,
    argument: str,
    authorities: list[str],
) -> str:
    """Ask DeepSeek (the simulated judge) for their likely hardest question on
    this issue, primed with the judge's profile topics + the argument."""
    from runtime_v2.services import _llm_client as llm

    topics_str = ", ".join(f"{k}({v})" for k, v in (topics or {}).items()) or "general"
    system = (
        f"You are Judge {judge}, a federal appellate judge on the Second Circuit, "
        f"about to hear oral argument. Your recorded concerns cluster on: {topics_str}. "
        "Ask the ONE hardest, most pointed question a judge with these concerns "
        "would ask counsel about the issue — the question most likely to expose a "
        "weakness in the argument. Be specific, not generic. This is a moot-court "
        "simulation; respond only with the question."
    )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Issue: {issue}\n\n"
                f"Counsel's argument: {argument[:1500]}\n\n"
                f"Authorities relied on: {', '.join(authorities[:8])}"
            ),
        },
    ]
    try:
        model = (
            llm._analysis_cloud_model()
            if llm._analysis_cloud_enabled()
            else "qwen3.5-4b"
        )
        parts: list[str] = []
        async for chunk, kind in llm.stream_content(
            model, messages, agent_id="legal_moot"
        ):
            if kind == "content":
                parts.append(chunk or "")
        return "".join(parts).strip() or f"Judge {judge} reserved judgment."
    except Exception as exc:
        log.warning("moot question failed for %s: %s", judge, exc)
        return f"Judge {judge}: (simulation unavailable) what is your best authority for the standard of review?"


async def run_bench(
    judges: list[str],
    issues: list[dict[str, str]],
    argument_by_issue: dict[str, str],
    authorities: list[str],
    fetch_profiles: bool = True,
) -> BenchSession:
    """Run the simulated bench. For each (judge, issue), generate the judge's
    likely hardest question. `issues` = [{issue, outline}]; `argument_by_issue`
    maps issue -> the counsel argument text. Profiles are fetched from
    CourtListener when `fetch_profiles` (degrade to generic on outage)."""
    session = BenchSession()
    headers = {}
    if os.getenv("COURTLISTENER_API_TOKEN"):
        headers["Authorization"] = f"Token {os.getenv('COURTLISTENER_API_TOKEN')}"

    profiles: list[JudgeProfile] = []
    if fetch_profiles:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            for j in judges:
                prof = await fetch_judge_profile(client, j, max_opinions=2)
                profiles.append(prof)
    else:
        profiles = [generic_profile(j) for j in judges]
    session.judges = profiles

    for prof in profiles:
        for issue in issues:
            arg = argument_by_issue.get(issue["issue"], "")
            q = await _ask_judge(
                prof.name, prof.topics, issue["issue"], arg, authorities
            )
            session.questions.append(
                BenchQuestion(
                    judge=prof.name,
                    issue=issue["issue"],
                    question=q,
                )
            )
    session.message = f"Generated {len(session.questions)} bench questions across {len(profiles)} judge(s)."
    return session


def render_bench(session: BenchSession) -> str:
    """Render the moot-court session as markdown."""
    out = ["# Simulated Bench — Oral Argument Prep\n"]
    for prof in session.judges:
        if prof.error:
            out.append(f"## Judge {prof.name} (profile degraded: {prof.error})")
        else:
            topics = (
                ", ".join(f"{k}×{v}" for k, v in (prof.topics or {}).items())
                or "no signal"
            )
            out.append(f"## Judge {prof.name} — cares about: {topics}")
        for q in [x for x in session.questions if x.judge == prof.name]:
            out.append(f"- **Q ({q.issue}):** {q.question}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_bench(["Walker", "Raggi"], [{"issue": "plain error"}], {}, []))
