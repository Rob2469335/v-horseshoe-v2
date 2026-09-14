# Lesson admissibility gate (design + wiring)

## Problem

Retrieval currently injects a `[PAST-MISTAKE WARNING]` on **similarity alone**. That
lets stale or wrong lessons shape decisions — *reflexion rot* actively degrades
performance — and LLM self-evaluation of a lesson's quality is unreliable.

## The gate

`swarm_os/services/lesson_admission.py` — a **deterministic, LLM-free** gate run
**before** any lesson is injected. Defaults: `min_confidence 0.6`, `min_success 1`,
`half_life_days 30`, `decay_floor 0.3`, `top_k 3`.

Per candidate (`admit`), reject unless ALL hold:

1. **Applicability** — `component == agent` OR `scope == "shared"`.
2. **Relevance** — a lesson about tool T fires only if T is in play this run.
3. **Confidence** — raw `confidence >= min_confidence`.
4. **Verified evidence** — `success_count >= min_success` (a belief is not enough).
5. **Recency** — `confidence * 0.5**(age/half_life) >= decay_floor`.

Then `admit_all`: conflict resolution (a candidate may declare `conflicts_with`;
the lower-evidence side is dropped) → `top_k` → **abstain** (inject nothing) if none
clear the bar. Abstention over a weak injection (arXiv:2604.27283).

## Wiring (deferred until the 400-run stops)

`swarm_os/services/reflection_loop.py::check_for_past_mistakes` becomes:

```
retrieve candidates (structured)  ->  lesson_admission.admit_all(...)  ->  format admitted only
```

- Needs **structured retrieval**: return `[{lesson_id, component, kind, scope,
  confidence, success_count, ts, correction, warned_tools}]`, not a formatted string
  (this is the existing `pathway`-capture follow-up).
- The **abstained** case injects nothing (and writes no `pathway` record).
- Log `admitted` + `rejected[].reasons` into the `pathway` record so admission
  precision is measurable.

### Rollout: shadow first

Ship **default-OFF with shadow mode** (redis/agent-memory #14 pattern): compute the
gate decision, log it, but keep injecting the current way. Compare `admitted` vs the
current hint on real runs, then enable. This avoids a silent behaviour change.

## Metrics (`qwen_train/pathway.py` extends)

- `admission_rate` — admitted / candidates.
- `injection_precision` — of admitted lessons whose warned signature was warned, the
  fraction later **avoided** (does the gate admit lessons that actually help?).
- `abstention_rate` — runs where the gate abstained.
- **Rot detection** — a lesson whose avoid-rate trends down gets demoted/expired
  (feeds `decay_floor` / evidence counters).

## Follow-ups (not in the module)

- **Structured retrieval** in `check_for_past_mistakes` (lesson ids/scores).
- **Evidence counters**: `success_count`/`failure_count` must actually be maintained
  when a lesson's warned failure is avoided vs repeated (otherwise criterion 4 is stale).
- **Automatic conflict detection** (today `conflicts_with` is declared explicitly).

## Why deterministic, not an LLM judge

Self-evaluator drift: an LLM-as-judge is too lenient and rarely rejects. The gate is
rule-based and auditable; the LLM is never asked whether its own lesson is good.
