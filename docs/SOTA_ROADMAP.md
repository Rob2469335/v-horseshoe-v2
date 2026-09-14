# SOTA roadmap: making the self-learning CLI the best

Deep-research synthesis (fan-out across swarm search + firecrawl + github + HF + arxiv;
**rate-limited** — several legs 429/timeout, so treat absences as missing evidence).
Load-bearing citations below were **independently fetched and verified** (marked ✅);
the rest are reported by the fan-out (marker: ~repo).

## 1. Where you actually stand (honest)

You are **not** alone. Self-improving coding agents are now a real category.

- **Hermes Agent** (NousResearch, ~repo) — closest competitor: "the agent that grows
  with you"; skill creation from experience + 4-layer memory (prompt/episodic/skills/
  user-model) + GEPA offline rewriting of underperforming skills (~40% faster / 40%
  fewer tokens at 20+ skills).
- **cl-agent** (~repo) — continual-learning substrate for Codex/Pi/Hermes/aider/SWE-agent/
  OpenHands: episode capture → replay → skill distillation → forward injection.
- **Continual Harness** (arXiv:2605.09998 ~repo) — reset-free in-context harness refinement.
- **Mendel Gödel Machine** (arXiv:2608.07645 ~repo) — evolve from the *archive* of attempts
  (clonal mutation + recombination), not one failure trajectory.
- **agentware** (~repo) — learning substrate bolted onto existing agents.

**Your defensible claim is not "nobody else is self-learning."** It is:

> **A coding CLI built around a verified closed-loop learning and self-healing
> architecture — with learning that lives *inside* the safety boundary.**

Your unique combination: per-turn trajectory capture + `state_hash` + step mining +
critical-step/recovery labeling + exact-scope tool policy + verified outcomes +
frozen-holdout measurement. That combination is what no single public project clearly ships.

## 2. The three findings that CHANGE the plan (verified ✅)

### 2.1 Gains often don't persist — report learning curves, never best-shot
- *Harness Updating Is Not Harness Benefit* — **arXiv:2605.30621** ✅
  Two separable capabilities: **harness-updating** (produce useful persistent updates)
  vs **harness-benefit** (benefit from them). Updating is **flat across model tiers**
  (even a 9B's updates rival Opus's). Benefit is **non-monotonic**: weak models benefit
  little, mid-tier most, strong less.
  → **Implication for you:** your worker is `robs4b` (weak/mid). It may **fail to activate
  or fail to follow** learned artifacts. The paper's guidance: *invest capability budget
  in the task-solving agent, and target harness-invocation + long-horizon instruction
  following.* Your T1→T2 may show flat gain **not because the loop is wrong but because
  the worker can't use the lessons** — that is a testable hypothesis, not an excuse.
- *Do Agent Optimizers Compound?* (~repo, Terminal-Bench 2.0) — most reported optimizer
  gains are **one-shot and do not persist** under repeated application.
  → Report the **learning curve** (you now do: `learning_curve`), not a single jump.

### 2.2 "Less is more" — extra machinery can degrade
- **AEL** (~repo): the **simplest** variant (reflection + memory; *no* credit assignment,
  *no* per-tool selection, *no* skills) scored best; added components degraded it.
- *Do Agent Optimizers Compound?* + *Harness Updating…* reinforce this.
  → **Implication:** your `tool_policy` / shortlist / bandit machinery must be **ablated
  per component**. Don't assume it helps. Experiment B generalizes: run ON/OFF for
  *each* mechanism, not just memory.

### 2.3 The benchmark you must match: matched on/off + pathway evidence
- **PAST-Bench** — **arXiv:2608.04003** ✅ — ordered fresh-session tasks under **matched
  conditions toggling retained experience on/off**, 26 scenarios / 204 episodes.
  Finding: **improvement is real but uneven; agents with the SAME headline gain differ in
  whether the gain follows the intended save/retrieve/update pathway.** They then built
  **Hermes+** with five targeted interventions and raised average gain.
  → **Implication:** T1→T2 alone is not enough. Add **pathway evidence** — did the lesson
  actually get retrieved and used? Your `state_hash` + per-turn capture gives you exactly
  the substrate PAST-Bench measures with. This is your chance to be *more* rigorous than
  the public projects: report not just *that* it improved, but *through what pathway*.

## 3. Trajectory → learning: the design to copy (verified ✅)

- **Trajectory-Informed Memory Generation** — **arXiv:2603.10600** ✅ — four components:
  (1) Trajectory Intelligence Extractor, (2) **Decision Attribution Analyzer** (which
  decisions caused failures/recoveries/inefficiencies), (3) Contextual Learning Generator
  emitting **three tip types** — *strategy* (from successes), *recovery* (from failures),
  *optimization* (from inefficient-but-successful runs) — with **provenance**, (4) adaptive
  memory retrieval by multi-dimensional similarity. Held-out AppWorld: **+14.3 pp** goal
  completion; **+28.5 pp** on complex tasks (149% relative).
  → This is nearly your miner's target design. Your `mine_gold.py` already produces
  success/recovery/critical tiers. **Add:** the third tip type (**optimization** from
  inefficient successes — e.g. high-`calls_to_success`), and **provenance** on each tip.

## 4. Other threads worth adopting (reported by fan-out)

- **Memory:** Mem0 (2504.19413), **Zep temporal KG** (2501.13956), MemSkill (2602.02474
  — make extract/consolidate/prune *learnable skills*), Mem-α (2509.25911 — RL the
  memory-construction policy). ⚠️ *When Does Memory Help?* (2609.05441) and *Cross-Scenario
  Generality of Agentic Memory* (2606.04315): memory is **not uniformly helpful** and often
  doesn't generalize → cost-aware, and Experiment B is the right test.
- **Tool selection:** chance-corrected **BoR** shortlist sizing (2605.24660) — you already
  use BoR/shortlist; OLIVIA online action adaptation (2605.11169); ToolRL (2504.13958).
  ⚠️ but see §2.2 — ablate.
- **Self-healing:** **VIGIL** sibling supervisor (2512.07094); **DARC** diagnosis-before-
  recovery + **prune mismatched interventions** (2608.11772); Self-Healing Agentic
  Orchestrators (2606.01416: timeout/malformed-args/stale-context/retry-loop/unverified-
  output → class → recovery). ⚠️ Codex CLI bug (~repo): verify the candidate **in
  isolation before touching the live job**; **define the give-up state**; report the
  **original** failure, not the monitor's later one.
- **Safety / verified self-improvement (your differentiator):** **ASG-SI** (2512.23760 —
  verifier-gated promotion of skills + evidence trails; names reward hacking, drift,
  non-modular improvement); SEVerA (verified self-evolving agents); *Learning to Undo*
  rollback (2510.14503); Safety Sidecar (ACL 2026 findings).
- **Benchmarks:** S3Gym (self-testing→improvement), EvoAgentBench (2607.05202 — ability
  **transfer**), SkillLearnBench (2604.20087), Meta-Agent Challenge (2606.04455).
- **Cautions to bake in:** reward hacking is measurable (ISOPRO: deterministic verifier ≫
  learned reward); memory poisoning/poisoned traces (ironclaw audit); **measurement fraud**
  — SkillsBench's eval split == train split (rllm issue), so **hold out for real**;
  catastrophic forgetting is the default (replay needs reward-scale care).

## 5. Prioritized roadmap mapped to your architecture

**P0 — measurement rigor (before any new feature).**
- Adopt PAST-Bench's **pathway evidence**: log, per lesson, whether it was *retrieved*,
  *activated*, and *followed* — not just that success moved. You have the per-turn data.
- Keep the **mix-adjusted** trend and the **held-out** discipline you already built.

**P0 — richer tips with provenance (extend `mine_gold.py`).**
- Add the **optimization tip** type (inefficient-but-successful: high `calls_to_success`
  that still verified) and attach provenance (run_id, state_hash, verdict) to every tip.

**P1 — the differentiator: verified self-improvement under the safety boundary.**
- Wrap lesson promotion in **ASG-SI-style verifier-gating + evidence trails**: each lesson
  records *where learned, verified how, how many times it worked, what state, has it ever
  failed, can it be rolled back, which tools it may influence*. (Your trust ledger/grants
  are the right home.) Then prove learning never weakens policy (a safety test T1→T2).

**P1 — self-healing done rigorously.**
- **Diagnosis before recovery** (DARC): classify the failure, *prune mismatched
  interventions*, then act. Add a **give-up state** and report the **original** failure.

**P2 — memory & tool policy, ablated.**
- Only after Experiment B: temporal-KG memory (Zep/Mem0), and per-mechanism ON/OFF
  ablations for the shortlist/bandit (AEL caution).

**P3 — model worker.**
- 2605.30621 says weak workers under-benefit. Either (a) train `robs4b` specifically on
  **harness-invocation + following** (Experiment C), or (b) deploy a mid-tier worker while
  the CLI learns. This reframes Experiment C: it isn't just "distill," it's "make the
  worker able to *use* the harness."

## 6. The claim to earn (and prove)

> **A coding CLI with a verified closed-loop learning and self-healing architecture —
> measured on a frozen held-out set, with pathway evidence, under a persistent safety
> boundary.**

Prove it with: Experiment A (curve + mix-adjusted + pathway), Experiment B (memory
ON/OFF), a safety test (learning never widens policy), and Experiment C (worker can use
the harness). That is a stronger and more honest position than "nobody else does this."

## 7. Coverage caveats (do not over-read)

- Fan-out was **rate-limited** (Semantic Scholar 429, arXiv API timeouts, firecrawl_search
  param error). **VPR, TRACE, AgentHER, SOAR, process reward models, agentware** have **no
  entry** in this digest — **missing evidence, not disconfirmation**.
- Only 2608.04003, 2603.10600, 2605.30621 were independently fetched here; the other IDs
  are as reported by the fan-out and should be verified before citing.
