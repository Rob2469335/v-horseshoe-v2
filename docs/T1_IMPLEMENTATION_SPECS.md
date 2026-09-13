# T1 Implementation Specs — Extension Substrate

**Status:** drafted 2026-09-13 while the learning curriculum runs. **Do NOT implement while a
curriculum run is active** — every item below edits files under the mined globs
(`runtime_v2/`, `swarm_os/`, `organism_console/`), and the run's filesystem items re-read those
files, so edits drift the expected answers → false failures. Implement **after** the run drains.

**Discipline for each item:** one logical commit; explicit staging; revert-proof test (prove the
test FAILS on pre-fix source); paste real ruff/pytest output; **one backend restart**, verifying
exactly one listener first (the duplicate-uvicorn zombie is the documented trap).

---

## ① Hooks (`PreToolUse` / `PostToolUse` / `Stop` / `session.compacting`)

**Current behavior:** no hook system. Tool calls dispatch straight through
`runtime_v2/services/tool_executor.py::run()`; the loop ends in
`agent_service_v2.py::_handle_final`; session history is capped in
`organism_console/state_store.py::save()`.

**Desired:** user-authored lifecycle hooks that can observe/deny/rewrite at defined points, with
**self-managing enable/disable** (watch-loop circuit-breaker pattern) — never self-modifying hook
*code*, only whether a hook stays enabled.

**Exact files / insertion points**
- **New:** `runtime_v2/services/hooks.py` — loader + event emitter.
- Pre/Post: `tool_executor.py:351` `async def run(` — emit `PreToolUse` before dispatch (stdout
  JSON may patch args; exit≠0 blocks), `PostToolUse` after (adds context).
- Stop: `agent_service_v2.py:1176` `_handle_final` — emit `Stop` when a final is accepted.
- session.compacting: `state_store.py:199` `save()` (around `_cap_history`, line 38) — emit
  `session.compacting` when the cap actually elides.

**Lifecycle / ownership**
- Config: `hooks.json` (repo) + `~/.config/rob/hooks.json`. **Treat `hooks.json` as untrusted repo
  content.** Each event: JSON payload on **stdin** to a user script; **exit≠0 blocks**; stdout JSON
  may rewrite (Pre) / add context (Post).
- Timeout + process kill: reuse the exact kill pattern in
  `swarm_os/capabilities/sandbox_repl.py` (`finally`-bound kill on timeout AND cancel).
- **CRITICAL ORDERING GUARANTEE (CVE-documented class):** hooks must **never be read, parsed, or
  executed before the trust/approval flow has completed for the repo/session** — Claude Code
  CVE-2025-59536 (8.7 HIGH, pre-trust code exec via hooks) and CVE-2026-21852 (pre-trust
  credential exfil via env config); Sonar's `core.fsmonitor` pre-trust vector. Add a test that
  tries to fire a hook pre-trust and confirms it's blocked.
- Disabled by default: `SWARM_HOOKS_ENABLED=0` — opt-in only.

**Self-management (the differentiator)**
- Track per-hook outcomes (timeout / non-zero / malformed) with the **same circuit-breaker
  counter/window** `watch_loop.py` uses for `tool_result` failures (extend/call into it — do not
  build a parallel mechanism).
- After N consecutive failures → **auto-disable that one hook** (fail-closed direction only) +
  an `[AUTO-REPAIR]`-format audit line via `watch_loop._audit_write`. Human re-enable required;
  never auto-re-enable; never rewrite the hook script.

**Failure/fallback:** any hook error → treat as no-op unless exit≠0 (then block). Hooks off →
behavior identical to today.

**Tests** (`tests/test_hooks.py`, new): block-on-nonzero; arg-rewrite applied; post-hook sees the
result; timeout kills; malformed stdout tolerated; breaker disables + audits after N; disabled
hook doesn't fire until manual re-enable; **pre-trust hook blocked**.

**Restart:** backend restart required (wired into the loop).

**Verification:** live — write one always-exit-1 test hook, confirm auto-disable + audit entry.

---

## ④ Deferred tool loading + `tool_search` (highest attention)

**Current behavior:** `stream_runner.get_tool_decision` always ships the agent's full allowed-tool
list into `_llm_prompts.build_tool_decision_system(allowed, mcp_schema)`. `tool_policy.shortlist`
(reorder/adaptive-depth) already exists but is flag-gated (`SWARM_TOOL_SHORTLIST`, currently 0).

**Desired:** above a threshold (~15 resident tools), stop shipping every schema; expose a
`tool_search` meta-action. Ranking = **BM25/IDF lexical** (`tool_policy.lexical_scores`) **+
outcome-fitness boost** (`get_active_genome().tool_genes`); cold-start = zero boost, **never a
penalty**. Per arXiv:2605.24660 (BoR) the signal should also inform **how many** tools are shown.

**Exact files / insertion points**
- Schema assembly: `runtime_v2/services/stream_runner.py` — the `allowed` list and
  `build_tool_decision_system(allowed, mcp_schema)` seam (~line 539 region; the shortlist hook is
  already at ~436-452).
- New action in BOTH enums: `runtime_v2/services/_llm_parser.py` (`TOOL_CALL_SCHEMA`) and
  `runtime_v2/services/_grammar_schema.py` (action enum, line ~26).
- **Sync test:** `tests/test_grammar_decode.py:151` asserts
  `len(gs["properties"]["action"]["enum"]) == 23` → bump to **24** when adding `tool_search`.
- Prompt: `runtime_v2/services/_llm_prompts.py` — document `tool_search`.
- Reuse: `runtime_v2/services/tool_policy.py` (`lexical_scores`, `rank`, `shortlist`).

**State passed into search:** the task text (last user message) + the shape (`shape_of`) + the
fitness genes. Deferred tools become available for subsequent turns once searched.

**Failure/fallback:** flag off → today's behavior exactly. `tool_search` failure → the resident
list is unchanged (fail-open to current tools).

**Tests** (new + extend): threshold flip (≤15 resident / >15 deferred); `tool_search` returns the
right schema; a deferred tool becomes callable after search; enum sync passes; fitness-boost
ordering with synthetic history; cold-start = lexical only.

**Restart:** backend restart required (schema/enums/prompt).

**Verification:** live with the flag on — a goal needing a below-threshold tool; confirm
`tool_search` finds and invokes it. Regression-test the agent-loop suites broadly (not just the new
tests) — this changes what the model sees on every call.

---

## ③ Real skills activation (`SKILL.md` body-load + gated widening)

**Current behavior:** `runtime_v2/services/skills_registry.py` is **metadata-only** (name +
one-line description); `system_prompts.py:192 _skills_context()` injects the metadata block for an
allowlisted role (currently `debugger`). The full body is intentionally never loaded.

**Desired:** load the **body** only when a touched file path matches the skill's `paths:`
frontmatter (conditional injection, not always-on); a registry listing each skill's trigger scope;
fail-open to `[]`.

**Widening (the differentiator, evidence-gated):**
- When an agent makes a mistake a **dormant** skill's description would plausibly have prevented,
  log a **skill-activation-gap** to ReflexionMemory (**causal justification from the trajectory**
  required — the *Honest Lying* confabulation finding, arXiv:2605.29463) — not a parallel store.
- Conservative threshold (e.g. 3 independent, causal gaps) → widen **one step** (strict path →
  broader glob; never straight to always-on).
- **Re-verify after N uses; auto-narrow on false-positive activations** — widening is
  **non-monotonic**. Every step audit-logged + manually reversible (edit frontmatter).

**Exact files / insertion points**
- `runtime_v2/services/skills_registry.py` — extend the frontmatter parser (`paths:`), add body
  load; keep the mtime cache + fail-open.
- `runtime_v2/services/system_prompts.py:192` `_skills_context()` — conditional body injection
  keyed on the run's read paths.
- Reuse `reflection_loop.store_success_lesson` / `store_reflexion` (kind-tagged) for gaps.
- Existing WATCH-ORACLE in AGENTS.md gates a *runtime* `skill_activate` action on logged evidence
  (3-touchpoint enum cost, like ④) — deferred until evidence exists.

**Precedence/conflict:** built-ins win; multiple matches → strictest (narrowest) path scope first,
then declaration order; a skill never overrides a system rule.

**Tests** (new): no body leak without a match; `paths:` triggers; multi-match precedence;
fail-open to `[]`; 3 causal gaps → exactly one widening step; false-positive activation narrows
back; audit entry written.

**Restart:** backend restart required (prompt assembly).

**Verification:** live — one skill with a narrow `paths:` trigger; a scenario that logs one gap;
confirm the gap is recorded end-to-end.

---

## Execution order after the run drains
1. **④ `tool_search`** (highest value; touches the most files + the enum-sync test).
2. **③ skills activation** (self-contained-ish; reuses ReflexionMemory).
3. **① hooks** (last; the one needing a dedicated security review before merge).

Each: edit → targeted tests + revert-proof → one backend restart (verify one listener) → live
verify → commit/push → update AGENTS.md "Recent Changes" only after acceptance.

**Reminder of the running constraint during any active curriculum run:** no edits to
`runtime_v2/`, `swarm_os/`, `organism_console/`; no backend restarts; don't touch
`data/evolution/tool_observations.jsonl`, `qwen_train/results/curriculum_runs.jsonl`, or
ReflexionMemory.
