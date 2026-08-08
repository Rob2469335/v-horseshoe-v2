# SOTA AI Legal Assistant — Merged Build Plan

**Source merge:** Perplexity deep-research (consumer legal assistant design + 2026 SOTA survey) · Gemini architecture (hybrid RAG + multi-agent swarm) · verified research corrections (real repos/APIs, not 404s).

**Overriding requirement (correction to Gemini's plan):** Gemini proposed a **greenfield FastAPI + Next.js** app. This is wrong for us. The app already exists: a **FastAPI backend** (`swarm_os/`) and a **React frontend** (`organism-console/` + `start-console/`). The legal assistant is a **new page + new backend module inside this existing app**, not a new codebase. Everything below is scoped to "add to the existing stack."

> ## ✅ DECISION GATE — ANSWERED (2026-08-08)
>
> **Q1 (portfolio vs real use): ANSWERED — (b) personal use by the operator.** The tool serves only the owner's own legal situations. **UPL is not the driving constraint** — self-help is not UPL. The real constraint is the operator personally acting on wrong information in their own situation (missed deadline, wrong jurisdiction, a believed-right that doesn't exist).
>
> **Q2 (live matter vs general-purpose): ANSWERED (2026-08-08) — general-purpose, NO live matter with a deadline.** Proceed with the reordered build (§9) on a normal cadence; no time pressure on any single fact. The standing rule still applies once it's in use: verify any specific fact you'd act on against the **official source** until citation-verification is built **AND** LegalCiteBench-evaluated — but there's no clock forcing reliance on an early build.
>
> **Scoping fact — ANSWERED (2026-08-08): jurisdictions = NY, NJ, GA, NC + federal.** Corpus slice is those four states' statutory codes + the U.S. Code/CFR + the relevant state/federal case law — NOT all-52-states. First workflow is jurisdiction-scoped accordingly (see §9).
>
> **Sequencing (per the personal-use reframe):** citation-verification is the primary safety mechanism and builds **right after the minimal vertical slice**, before dockets/calendar (§9, M4 promoted). Safety/UPL *labeling* agent is de-prioritized — the checks that matter are the citation checks, not a banner.

---

## 0. What "SOTA" means here (and what it does NOT)

SOTA legal AI in 2026 = **RAG over a deep legal corpus + agentic workflows + a strict citation-verification subsystem**. Harvey / CoCounsel / Lexis+ AI are exactly that stack with proprietary Westlaw/Lexis corpora. We cannot and should not replicate their corpus. But independent benchmarks show a frontier LLM + strong retrieval + verification **beats them on focused workflows**. The moat is **verifiable citations**, not corpus ownership.

- Target: match/beat CoCounsel-class **research + drafting** on 2–3 narrow consumer workflows (start: small claims + landlord-tenant), **scoped to NY · NJ · GA · NC + federal** for the operator's personal use.
- Reality check: commercial-tool hallucination audits are **3–17%** depending on task/jurisdiction. **LegalCiteBench's published numbers are worse than that framing suggests**: the best model scored **6.8/100 on closed-book citation retrieval and 6.35/100 on citation completion** — not "occasionally wrong" but *cannot do the task at all without external grounding*. That strengthens this plan's central thesis: **independent citation re-verification is the product, not a feature.**

---

## 1. Verified data sources (real URLs — corrections applied)

| Corpus | Source | Access | Verified |
|--------|--------|--------|----------|
| Case law (full text) | **Caselaw Access Project (CAP)** — case.law (Harvard) | API + bulk; ~6.4M cases; rate limit ~500 full-text/day standard, research access for bulk | ✓ case.law reachable |
| Opinions + dockets + metadata | **CourtListener / Free Law Project** | REST API v4, bulk CSVs, webhooks, **official MCP server** | ✓ courtlistener.com/help/api |
| Federal dockets via PACER | **RECAP** (through CourtListener) | REST API (pays PACER fees, archives openly) | ✓ via CourtListener |
| State/federal statutes + constitutions | **Open US Law** — `github.com/Vaquill-AI/open-us-law` | 2M+ statutory sections, USC + state codes + constitutions; Apache-2.0 scripts / CC-BY data; active (updated hours ago) | ✓ verified this session + independently re-confirmed |
| Citation-verification eval | **LegalCiteBench** — `github.com/Sijia711/LegalCiteBench` | Clean benchmark w/ deliberately-altered citations, MIT; paper arXiv:2605.10186, accepted AI4Law @ ICML 2026 | ✓ verified + independently re-confirmed |
| Phantom-citation eval | **LePhantomCite** | Referenced in SOTA research; **verify exact repo before integration** (not independently confirmed) | ⚠️ verify |

**Corrections to Gemini's plan:**
- Gemini said "**OpenRegs**" for statutes — wrong primary source (historical, scraped). Use **Open US Law** (`Vaquill-AI/open-us-law`).
- **Vendor nuance (confirmed):** Open US Law's data/scripts are genuinely open (Apache-2.0/CC-BY), BUT it's the open-sourced data layer of **Vaquill AI**, a commercial legal-tech company selling a paid product on top of it (app.vaquill.ai, BYOK API keys). Fine to build against — but it is a vendor's open-sourced infra, **not** a neutral nonprofit archive the way CourtListener/Free Law Project is. Prefer CourtListener/CAP for anything where independence matters; treat Open US Law as a data feed with a commercial upstream, and don't route through their paid API unless deliberately chosen.
- Gemini said "academic LegalCiteBench dataset" with no pointer — actual repo is `Sijia711/LegalCiteBench`.
- Gemini said "webhooks from CourtListener APIs" — correct, and there's also an **official CourtListener MCP server**, which drops directly into this repo's existing MCP tool registry (`swarm_os/lib/mcp/`). High-value reuse.
- **No scraping of random sites.** APIs/bulk only. Secondary sources (practice guides, blogs) kept separate from primary law.

---

## 2. Feature set (merged: Perplexity UX depth + Gemini workflow)

1. **Intake / triage** — plain-English narrative → structured fact pattern → jurisdiction + issue class (small claims, landlord-tenant, wage theft, traffic). Emergency detection (imminent harm/arrest/court deadline) → hard-stop to human/emergency services. Decides *help* vs *refer* (UPL gate).
2. **Plain-language explanations** — statutes + court rules summarized, jurisdiction-labeled, every proposition carries a citation.
3. **Citation-grounded research** — "top statutes + relevant cases for your situation, with summaries." Non-technical summary + full text on tap + official-source link.
4. **Document / form generation** — demand letters, response letters, simple contracts (roommate/N NDA), small-claims and traffic self-help filings. Jurisdiction templates + fact-pattern personalization, labeled "draft — lawyer review."
5. **Case & statute explorer** (power users) — search by citation/party/docket/keyword, filter by court/jurisdiction/date, opinion text + metadata.
6. **Docket dashboard + calendar** — tracked cases, timeline, "next hearing/deadline," via CourtListener dockets API + webhooks (Perplexity's docket/calendar primitive — normalized docket entries → calendar events). PACER-privacy rules respected.
7. **Safety layer** — prominent non-lawyer disclaimer, "Show sources" + "Why am I seeing this?" on every answer, one-click lawyer-referral escalation, full trace/audit export.

---

## 3. Architecture (merged, mapped onto the EXISTING stack)

```
┌─ Frontend (organism-console/src/pages/LegalAssistantPage.tsx — NEW page) ─┐
│  chat + sources side-panel │ research tab │ docket dashboard │ trace view  │
└──────────────┬────────────────────────────────────────────────────────────┘
               │ HTTP / SSE
┌─ Backend (swarm_os/ — NEW legal module) ──────────────────────────────────┐
│  api/legal_routes.py      — /legal/intake /research /draft /verify /docket │
│  services/legal/          — NEW package                                    │
│    intake.py              — fact pattern + jurisdiction + issue classify   │
│    planner.py             — sub-research decomposition                     │
│    research.py            — hybrid RAG loop (existing Qdrant stack)        │
│    synthesis.py           — citation-grounded memo/answer draft            │
│    citation_verify.py     — RE-RETRIEVE every citation; existence+alignment│
│    safety.py              — UPL / jurisdiction / disclaimer checks         │
│    docket.py              — CourtListener dockets + webhook consumer       │
│    ingestion.py           — CAP / CourtListener / OpenUSLaw ETL → Qdrant   │
│  lib/mcp/courtlistener.py — register official CourtListener MCP server     │
└──────────────┬────────────────────────────────────────────────────────────┘
               │ hybrid retrieval (REUSE existing infra)
┌─ Reuse from THIS repo ────────────────────────────────────────────────────┐
│  Qdrant + vector_store.py        (dense embeddings, already running)      │
│  tool_registry / MCP registry    (CourtListener MCP drops in)             │
│  agent orchestration (agent_service_v2, tool_executor)                    │
│  cloud+local LLM routing (fallback_manager, _llm_client)                  │
│  tracing (control_plane/trace, api_features SSE)                          │
└───────────────────────────────────────────────────────────────────────────┘
```

**Key merge decision:** reuse the existing Qdrant/agent/LLM/tracing infra instead of building new plumbing. The net-new work is the **legal corpus ingestion**, **legal retrieval filters**, and the **citation-verification + safety agents**.

---

## 4. Multi-agent swarm topology (Gemini's, kept — it's correct)

Fixed workflow graph, not one monolithic prompt:

```
intake → planner → research (RAG loop) → synthesis → citation-verify → safety-eval
```

- **Intake agent** — narrative → fact pattern, jurisdiction, issue class; UPL gate.
- **Planner agent** — decompose into sub-research tasks, set precedence.
- **Research agent** — hybrid retrieval (BM25 + dense) over Qdrant; jurisdiction/court/date filters; rerank.
- **Synthesis agent** — drafts answer strictly from retrieved contexts; emits proposition→authority→span mapping.
- **Citation-verification agent** — independent cross-exam of EVERY citation: exists? correct court/jurisdiction/date? supports the proposition? Uses LegalCiteBench-style error taxonomy (fabricated / misquoted / misattributed / wrong posture).
- **Safety/eval agent** — jurisdiction leakage, outdated law, missing disclaimer, UPL risk; blocks or downgrades + surfaces warning in UI.

This maps directly onto this repo's `_agent_routing.py` / `agent_service_v2.py` delegation pattern — the agents are new, the orchestration loop is existing machinery.

---

## 5. Citation verification (THE moat — this is what makes it SOTA)

Synthesis must output **structured citations**: `[{proposition, authority_id, quoted_span}]`. The verify agent then, **independently of synthesis**:

1. Re-retrieve each authority by its citation (CAP/CourtListener/statutes corpus).
2. Check existence + metadata (court, jurisdiction, date, precedential status).
3. Check the quoted span actually contains the proposition (semantic alignment, not just string match).
4. Classify failures using LegalCiteBench error taxonomy; **block** fabricated/unverifiable citations, **flag** misaligned ones.
5. Eval on `Sijia711/LegalCiteBench` clean set in CI (see §7).

**Billing/cooldown note (repo convention):** the LLM calls in this module go through the existing `fallback_manager`; keep the fail-closed pin behavior for permanent errors (per AGENTS.md documented intent).

---

## 6. The Page (organism-console/src/pages/LegalAssistantPage.tsx)

Follows the existing page pattern (same shape as `CommandCenterPage` / `WorkspacePage`):

- **Left:** chat/intake (SSE stream, matching `api_features` SSE pattern used by `DebateRoomPanel`).
- **Right:** sources panel — click any inline citation → quoted paragraph from the retrieved case/statute + official link. Renders the trace (retrieval → contexts → draft → verification score).
- **Tabs:** `Ask` (chat) · `Research` (explorer) · `Documents` (drafts) · `My Cases` (docket dashboard + calendar).
- **Safety chrome:** persistent "not a lawyer, not legal advice" banner; per-answer "Show sources / Why am I seeing this?"; escalate-to-lawyer button.
- Nav: registered in the console's nav/sidebar alongside the existing pages.

---

## 7. Evaluation & CI (Gemini + Perplexity agree; make it gating)

- Build **200–500 query eval set** (real small-claims/landlord-tenant scenarios), lawyer-labeled for: citation correctness, jurisdictional fit, safety/UPL.
- Track against: raw LLM (no RAG) vs RAG (no verify) vs full agentic+verify stack.
- **LegalCiteBench in CI** (`Sijia711/LegalCiteBench`): citation retrieval + error detection pass/fail gates.
- Metrics: citation accuracy, hallucination rate, jurisdiction correctness, answer correctness, latency.
- **Trace-first:** every run emits prompt → retrieval queries → top-K contexts → draft → verification score (repo's existing trace infra). Block deploys on regressions.

---

## 8. Safety / guardrails (reframed for self-use)

**UPL is not the driving constraint for a self-help tool** (representing yourself is not UPL). The real constraint is **operator reliance on wrong information** — a missed deadline, wrong jurisdiction, a believed-right that doesn't exist. So:

- **Citation-verification is the primary safety mechanism** (it checks the information, not the label) — see §4, promoted to right-after-slice in §9.
- **Verify against official sources before acting:** the tool is a research aid, never the final check on a real deadline/filing; the court's own site / the actual statute is authoritative until citation-verification is built AND LegalCiteBench-evaluated.
- Education + triage + drafting support, never representation of others.
- Prominent "not legal advice, verify against official sources" note.
- Emergency/arrest/deadline detection → hard-stop to a human.
- Primary authority vs secondary content separated in data model and UI.
- Audit log of every query + retrieved sources, exportable.
- PACER/RECAP privacy constraints respected for dockets.
- **If the tool ever serves real people (not just the operator), the UPL line returns** and needs qualified legal input before that ships — see DECISION GATE.

---

## 9. Build order (milestones) — REORDERED for self-use, scoped to **NY · NJ · GA · NC + federal**

**Sequencing flip (per the UPL reframe):** the safety/UPL agent is now lower stakes (it's just you), so the **citation-verification agent moves up** — it's the real check protecting your own decisions, not a compliance checkbox. It gets built and LegalCiteBench-evaluated **immediately after the minimal vertical slice**, before dockets/calendar.

1. **Corpus ingestion (scoped to the 5 jurisdictions, NOT all 52 states):**
   - Statutes: OpenUSLaw → Qdrant, filtered to **NY Consolidated Laws, NJ Revised Statutes, GA Official Code, NC General Statutes, + USC/CFR** — section-level chunks, with `jurisdiction`/`title`/`chapter`/`section` payload fields.
   - Case law: CAP + CourtListener → Qdrant for the **4 states' courts + federal** (district/circuit/SCOTUS) — paragraph-level chunks, with `jurisdiction`/`court`/`date`/`precedential_status` payload fields.
   - CourtListener MCP into `lib/mcp/`.
   - **No scraping; APIs/bulk only.** NY/NJ/GA/NC statutes come from OpenUSLaw's dataset; note its commercial (Vaquill AI) upstream (§1).
2. **Hybrid retrieval** legal-tuned: BM25 + dense, jurisdiction filters (NY/NJ/GA/NC/federal), rerank (existing reranker infra at :8082).
3. **Intake → research → synthesis** minimal vertical slice for ONE workflow in one jurisdiction (start: **NY small claims**, which maps to NY's small-claims court structure), wired as `/legal/*` routes + page.
4. **Citation-verification agent + LegalCiteBench CI gate** ← *promoted; the primary safety mechanism, not a checkbox.*
5. **Dockets/calendar** via CourtListener webhooks (federal PACER/RECAP + state coverage where available).
6. **Safety/eval agent** + disclaimer + audit UI (de-prioritized for self-use — the checks that matter are §4's, not a label).
7. Expand to the remaining jurisdictions (NJ → GA → NC), then the second workflow (landlord-tenant), then broaden.

**First slice definition (NY small claims):** intake classifies the narrative → NY small-claims issue → research retrieves NY statutes (e.g. NY General Obligations Law, UCC exemptions) + NY small-claims court rules + relevant NY/federal cases → synthesis drafts an answer with per-proposition citations → verification cross-checks every citation. This is deliberately ONE state, ONE workflow — the fastest path to a verifiable, best-in-class slice, with the corpus/coverage expanding outward from it.

---

## 10. Corrections Claude should be told (so it doesn't rebuild the wrong thing)

1. **Not greenfield.** Existing FastAPI (`swarm_os/`) + React (`organism-console/`) app; this is a new page + backend module.
2. **Statutes source is Open US Law** (`Vaquill-AI/open-us-law`), not OpenRegs.
3. **LegalCiteBench repo** is `Sijia711/LegalCiteBench`.
4. **CourtListener has an official MCP server** — reuse the repo's MCP registry.
5. Reuse existing Qdrant, agent-orchestration, LLM-routing, and tracing instead of re-implementing.
6. LePhantomCite URL unverified — confirm before wiring it into CI.
