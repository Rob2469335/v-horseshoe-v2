# Run #2 — live observations, fixes, and SOTA improvements (2026-09-13)

Captured while run #2 is live (`--allow-approval`, cache+shortlist ON, grants sandbox_repl/lsp/git).

## Live status (observed)
- `[659/5000]`, **98% verified (59/60)**, 0 `ineligible`, 0 CLI failures, `obs≈982`, ~15 s/item.
- **`sandbox_repl` items now PASS** — the scoped grant works; cache ON, no loops. Run #1 was ~80% on filesystem only.

## Defects to fix (small, concrete)
1. **`recovery` flag is a false positive (59/60).** Definition `verified AND used−target ≠ ∅` counts
   the agent's *habitual* `filesystem` use on sandbox items, so almost every item is "recovery".
   **Fix:** derive recovery from the TRAJECTORY — a tool FAILED/DENIED mid-run, then a LATER
   different tool succeeded, and the run verified. Requires per-turn records (see #4). Until then,
   **drop the flag** (it is noise, not signal).
2. **`/readyz` false "hung".** Its `llamacpp_reachable` probe times out while the single model slot
   is busy → readiness looks down while the app is serving fine (this run is 98% passing). **Fix:**
   short-timeout the probe and treat "slot busy/queued" as reachable, or split liveness from
   model-availability. (Distinguish this from the genuine duplicate-uvicorn wedge.)
3. **Duplicate-backend guard.** The real wedge was **two uvicorns on :8000** (loop idle, `/readyz`
   hangs, runs stall). **Fix:** a single-instance lock at startup (or a port-owner check) so a
   second backend refuses to bind / exits with a clear message.

## SOTA improvements (research-backed, prioritized)
4. **Per-turn `(tool, args, observation, outcome)` records** — the single biggest gap. Unlocks real
   recovery detection (#1), **critical-step mining** (VPR arXiv:2605.10325; TRACE 2607.13988), and
   recovery-as-gold (AgentHER 2603.21357). Extend each run record additively with a `turns` array.
5. **Tool-selection metric (the "did the policy improve" gate).** We score the ANSWER, not whether
   the agent chose well among the viable tools. Add: `calls_to_success`, `intended_tool_used`, and
   for choice-forcing items whether it picked the cheapest adequate tool. Measured on the FROZEN
   holdout before/after.
6. **Diversity score** — track tool × shape × trajectory-length diversity per run, to *prove*
   diversity (TDScaling arXiv:2602.03219) rather than assert it.
7. **Cache-hit + off-peak/peak cost per run** — surface from `usage_log` at run end
   (cache-hit % and the peak-hour slice), so cost is measured, not estimated ($0.00045/call measured).
8. **Shape/tool balance** — pool is **sandbox-heavy (1,933 vs 1,363 filesystem)**. Rebalance the
   re-mine if a future run should weight exploration/verification equally.
9. **Shortlist A/B.** `SWARM_TOOL_SHORTLIST=1` is ON but unproven — add an on-vs-off comparison on
   the frozen holdout to confirm it *helps* (not hurts) selection.
10. **Distillation path (endgame):** per-turn records → **critical-step + recovery miner** →
    **AMD (2608.07169) / CLPD (2605.11260) teacher→hierarchical-memory→student** → **LoRA on the
    curated gold set**. Run #2 is the data-collection front half that makes this useful.

## Priority
1. **Per-turn records** (unlocks 1, 4, 10)
2. **`/readyz` probe + duplicate-backend guard** (operational reliability)
3. **Tool-selection + diversity metrics** (the measurement gate)
4. **Recovery-flag fix** (or drop it)
5. **Shortlist A/B + pool rebalance**
