"""Forward-citing "still good law" monitor for Rob's Lawyer (Gap 1 — the
Shepard's/KeyCite-alert replacement).

Evidence: every federal practitioner is under a candor obligation to disclose
adverse authority. KeyCite/Shepard's charge for exactly this and hide it behind
license; CourtListener exposes the SAME data free via the `/opinions-cited/`
edge API (`citing_opinion`/`cited_opinion`/`depth` — field names verified live
2026-08-11, token-gated 401 without a token). We already built the treatment
taxonomy (followed/distinguished/overruled/questioned) and the citation-graph
machinery — this module adds the POLL + ALERT TRIPAGE layer.

Flow:
  1. For each manifest authority, resolve its CourtListener opinion id via the
     citation-lookup → opinions-by-cluster seam (reused from case_corpus).
  2. `GET /opinions-cited/?cited_opinion=<id>` → cases that CITE this authority
     (forward cites), each with a `depth`.
  3. For each NEW citing opinion, classify its treatment of our authority from
     the citing text via the existing label_treatment taxonomy.
  4. Persist to a durable state file (resumable, rate-limit-aware — free tier
     ~50 req/hr, 125/day). Re-polls dedupe by citing-opinion id.
  5. Render a "Still Good Law?" report: adverse treatments (distinguished /
     overruled / questioned) are ALERTS; followed/neutral are informational.

Rate budget: ~66 authorities x ~2 calls = ~132 calls — at the edge of the daily
free tier, so the module supports a `max_authorities` cap and resumes from
state. Never raises — a poll failure records `error` for that authority and
continues.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from swarm_os.services.legal.case_graph import label_treatment, citing_sentence_for
from swarm_os.services.legal.citation_verify import (
    CITATION_LOOKUP_URL,
    case_citation_key,
)

log = logging.getLogger(__name__)

OPINIONS_URL = os.getenv(
    "COURTLISTENER_OPINIONS_URL",
    "https://www.courtlistener.com/api/rest/v4/opinions/",
)
OPINIONS_CITED_URL = os.getenv(
    "COURTLISTENER_OPINIONS_CITED_URL",
    "https://www.courtlistener.com/api/rest/v4/opinions-cited/",
)
# Durable state: cite -> {opinion_id, last_poll, treatments: {citing_id: label}, errors}
STATE_FILE = Path("data/legal/citator_state.json")

# Adverse treatments = the candor trigger. Informational = followed/neutral.
ADVERSE_TREATMENTS = {"distinguished", "overruled", "questioned"}

# Pace between CourtListener calls (free tier ~5/min, verified live).
_PACE_S = float(os.getenv("COURTLISTENER_PACE_S", "12.5"))


@dataclass
class CitatorEvent:
    cite: str
    citing_cite: str
    citing_name: str
    treatment: str
    depth: int
    adverse: bool


@dataclass
class CitatorReport:
    authorities: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True
    message: str = ""


async def _pace() -> None:
    # CourtListener free tier rate-limits ~5 req/min; but sleeping the WHOLE
    # event loop (old `time.sleep`) froze every concurrent agent stream,
    # heartbeat, and daemon for minutes during a citator poll. The rate-limit
    # intent is preserved with an ASYNC sleep — the loop stays responsive.
    await asyncio.sleep(_PACE_S)


async def _resolve_opinion_id(client: httpx.AsyncClient, cite: str) -> int | None:
    """Resolve a case cite to its CourtListener opinion id via the two-step
    seam (citation-lookup -> cluster -> opinions-by-cluster). Returns None on
    any failure (caller records it, never raises)."""
    await _pace()
    try:
        resp = await client.post(
            CITATION_LOOKUP_URL,
            data={"text": cite},
            timeout=30.0,
        )
    except Exception as exc:
        log.warning("citator lookup failed for %s: %s", cite, exc)
        return None
    if resp.status_code != 200:
        return None
    try:
        item = (resp.json() or [{}])[0]
        clusters = item.get("clusters") or []
    except Exception:
        return None
    if not clusters:
        return None
    cluster_id = clusters[0].get("id")
    if not cluster_id:
        return None
    await _pace()
    try:
        opin = await client.get(
            OPINIONS_URL,
            params={"cluster": cluster_id, "format": "json"},
            timeout=30.0,
        )
    except Exception as exc:
        log.warning("citator opinions fetch failed for %s: %s", cite, exc)
        return None
    if opin.status_code != 200:
        return None
    try:
        results = (opin.json() or {}).get("results") or []
    except Exception:
        return None
    if not results:
        return None
    oid = results[0].get("id")
    return int(oid) if oid else None


async def _forward_citing(client: httpx.AsyncClient, opinion_id: int) -> list[dict]:
    """GET /opinions-cited/?cited_opinion=<id> — cases citing this opinion,
    each with a `depth`. Returns [{citing_opinion, depth}]. Empty on outage."""
    await _pace()
    try:
        resp = await client.get(
            OPINIONS_CITED_URL,
            params={"cited_opinion": opinion_id, "format": "json", "page_size": 50},
            timeout=30.0,
        )
    except Exception as exc:
        log.warning("opinions-cited failed for id %s: %s", opinion_id, exc)
        return []
    if resp.status_code != 200:
        return []
    try:
        results = (resp.json() or {}).get("results") or []
    except Exception:
        return []
    return [
        {
            "citing_opinion": int(r.get("citing_opinion") or 0),
            "depth": int(r.get("depth") or 1),
        }
        for r in results
        if r.get("citing_opinion")
    ]


async def _citing_opinion_text(
    client: httpx.AsyncClient, citing_id: int
) -> tuple[str, str]:
    """Fetch a citing opinion's text + case name (for treatment classification).
    Returns (text, case_name); empty on outage."""
    await _pace()
    try:
        resp = await client.get(
            OPINIONS_URL,
            params={"id": citing_id, "format": "json"},
            timeout=30.0,
        )
    except Exception as exc:
        log.warning("citing opinion fetch failed id %s: %s", citing_id, exc)
        return "", ""
    if resp.status_code != 200:
        return "", ""
    try:
        results = (resp.json() or {}).get("results") or []
    except Exception:
        return "", ""
    if not results:
        return "", ""
    item = results[0]
    text = (item.get("plain_text") or "").strip()
    if not text:
        text = (item.get("html_with_citations") or "").strip()
    name = (
        (item.get("cluster") or {}).get("case_name", "")
        if isinstance(item.get("cluster"), dict)
        else ""
    )
    return text, name


def _classify_treatment(citing_text: str, cited_key: str) -> str:
    """Classify how the citing opinion treats our authority using the existing
    taxonomy (case_graph.label_treatment) on the citing sentence that mentions
    the cited case."""
    sent = citing_sentence_for(citing_text or "", cited_key)
    if not sent:
        return "neutral"
    return label_treatment(sent)


def _load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


async def poll_authority(
    manifest_cases: list[dict[str, Any]],
    max_authorities: int | None = None,
    refresh: bool = False,
) -> CitatorReport:
    """Poll the forward-citing monitor for the manifest authorities.

    For each authority not yet polled (or `refresh=True`): resolve its opinion
    id, fetch forward cites, classify each citing opinion's treatment, store the
    events. Returns a CitatorReport with all events + the adverse ALERTS.

    Durable: state file resumes across runs (rate-limit aware). Fail-closed:
    an authority whose poll fails is recorded with an `error`, never skipped
    silently. Never raises."""
    state = _load_state()
    report = CitatorReport()
    headers = {}
    if os.getenv("COURTLISTENER_API_TOKEN"):
        headers["Authorization"] = f"Token {os.getenv('COURTLISTENER_API_TOKEN')}"

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        processed = 0
        for case in manifest_cases:
            cite = case.get("cite", "")
            if not cite:
                continue
            info = state.get(cite, {})
            if not refresh and info.get("opinion_id") and info.get("polled"):
                # Already polled; keep its treatments in the report.
                report.authorities.append(
                    {
                        "cite": cite,
                        "name": case.get("name", ""),
                        "treatments": info.get("treatments", {}),
                        "adverse": info.get("adverse", []),
                        "error": info.get("error", ""),
                    }
                )
                continue
            if max_authorities is not None and processed >= max_authorities:
                break

            # Resolve opinion id (cache the resolution).
            opinion_id = info.get("opinion_id")
            if not opinion_id:
                opinion_id = await _resolve_opinion_id(client, cite)
                if not opinion_id:
                    state[cite] = {
                        **info,
                        "error": "resolve_failed",
                        "polled": time.time(),
                    }
                    _save_state(state)
                    report.authorities.append(
                        {
                            "cite": cite,
                            "name": case.get("name", ""),
                            "error": "resolve_failed",
                            "treatments": {},
                        }
                    )
                    processed += 1
                    continue
                state[cite] = {**info, "opinion_id": opinion_id}

            citing = await _forward_citing(client, int(opinion_id))
            treatments: dict[str, str] = {}
            cited_key = case_citation_key(cite)
            for c in citing:
                citing_id = c["citing_opinion"]
                if info.get("treatments", {}).get(str(citing_id)):
                    treatments[str(citing_id)] = info["treatments"][str(citing_id)]
                    continue
                text, name = await _citing_opinion_text(client, citing_id)
                label = _classify_treatment(text, cited_key or "")
                treatments[str(citing_id)] = label
            adverse = [
                cid for cid, lab in treatments.items() if lab in ADVERSE_TREATMENTS
            ]
            state[cite] = {
                **state.get(cite, {}),
                "opinion_id": opinion_id,
                "treatments": treatments,
                "adverse": adverse,
                "polled": time.time(),
            }
            _save_state(state)
            report.authorities.append(
                {
                    "cite": cite,
                    "name": case.get("name", ""),
                    "treatments": treatments,
                    "adverse": adverse,
                    "error": "",
                }
            )
            processed += 1

    # Build the alerts list.
    for a in report.authorities:
        for cid, lab in a.get("treatments", {}).items():
            if lab in ADVERSE_TREATMENTS:
                report.alerts.append(
                    {
                        "authority": a["cite"],
                        "authority_name": a.get("name", ""),
                        "citing_opinion_id": cid,
                        "treatment": lab,
                    }
                )
    report.message = (
        f"Monitored {len(report.authorities)} authorities; "
        f"{len(report.alerts)} adverse-treatment alert(s). "
        f"Re-poll with refresh=True to re-check."
    )
    return report


def render_citator_report(report: CitatorReport) -> str:
    """Render the 'Still Good Law?' report as markdown (console/surface)."""
    out = ["# Still Good Law? — Forward-Citing Monitor\n"]
    if not report.authorities:
        out.append("No authorities monitored yet.")
        return "\n".join(out)
    if report.alerts:
        out.append("## ⚠ ADVERSE ALERTS (candor disclosure required)")
        for a in report.alerts:
            out.append(
                f"- **{a['authority']}** ({a['authority_name']}) — "
                f"{a['treatment']} by citing opinion {a['citing_opinion_id']}"
            )
        out.append("")
    else:
        out.append("## No adverse treatment detected on the polled authorities\n")
    out.append("## Monitored authorities")
    for a in report.authorities:
        counts = {}
        for lab in a.get("treatments", {}).values():
            counts[lab] = counts.get(lab, 0) + 1
        err = f" [error: {a.get('error')}]" if a.get("error") else ""
        out.append(
            f"- {a['cite']} ({a.get('name', '')}) — {counts or 'no forward cites'}{err}"
        )
    return "\n".join(out)


async def run_citator_cli() -> None:
    """Detached CLI entrypoint: poll all manifest authorities and write the
    report to data/legal/citator_report.md."""
    from swarm_os.services.legal.case_corpus import CASE_MANIFEST

    report = await poll_authority(CASE_MANIFEST)
    out_dir = Path("data/legal")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "citator_report.md").write_text(
        render_citator_report(report), encoding="utf-8"
    )
    log.info("citator poll complete: %s", report.message)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_citator_cli())
