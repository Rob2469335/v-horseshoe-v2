# Pathway evidence: proving *how* the CLI improved, not just *that* it did

## Why

PAST-Bench (**arXiv:2608.04003**, verified) found that agents with the **same headline
gain differ in whether the gain followed the intended save→retrieve→update pathway**.
And *Harness Updating Is Not Harness Benefit* (**arXiv:2605.30621**, verified) found the
benefit is **non-monotonic in model capability** — weak workers often **fail to activate
or fail to follow** learned artifacts.

So on a frozen 60, a T1→T2 move is not enough. We must show the move came **through the
learning pathway**, and separately measure whether our worker can *use* a lesson at all.

## The pathway, mapped to THIS system

```
SAVE      reflexion_loop.store_reflexion / store_success_lesson → Qdrant ReflexionMemory
          (deterministic point id; payload: component, kind, correction, do_not_repeat,
           scope, confidence)

RETRIEVE  stream_runner: check_for_past_mistakes(query) → hint → injected into the system
          prompt as "[PAST-MISTAKE WARNING]"

ACTIVATE  the run's decisions actually avoid the warned failure (proxy — see limits)

UPDATE    the run reaches a VERIFIED success (the lesson paid off)
```

## What we capture today vs what's missing

| Phase | Captured now | Missing |
|---|---|---|
| SAVE | the rule lives in Qdrant (has provenance: component/kind/confidence) | linkage run→lesson written |
| RETRIEVE | nothing | **the retrieval + injection event** |
| ACTIVATE | nothing | whether the warned failure was avoided |
| UPDATE | run `verified` + per-turn steps + `state_hash` | (derivable once RETRIEVE is logged) |

The gap is a single **`pathway` record** appended to the run's trajectory file.

## The `pathway` record (schema)

Appended to `data/trajectories/<run_id>.jsonl` (same file as steps + summary; summary
stays LAST). Written at the injection site.

```json
{
  "record_type": "pathway",
  "run_id": "…",
  "phase": "retrieve",
  "injected": true,
  "injected_chars": 240,
  "turn": 1,
  "lessons": [
    {"lesson_id": "…", "score": 0.83, "component": "coder",
     "kind": "failure", "scope": "shared", "correction": "list the parent dir first"}
  ],
  "warned_signatures": ["filesystem:not_found"],
  "warned_tools": ["filesystem"]
}
```

- `warned_signatures` = coarse `tool:err_class` keys (timeout / not_found / permission /
  malformed / other) derived from the retrieved lesson's correction/failure_reason, so
  they can be matched against observed step failures **without brittle string equality**.
- When no hint is injected we write nothing (absence = no retrieval).
- Implemented in `stream_runner.py` at the `check_for_past_mistakes` site (~line 599) —
  **applied after the 400-run** (mined-answer-drift rule).

## Derived metrics (`qwen_train/pathway.py`)

- `retrieval_rate` — runs with a `pathway` record / total runs. (Was the lesson even fetched?)
- `injection_rate` — injections / retrieval attempts.
- `avoidance_rate` — of runs warned about signature S, the fraction that did **not** hit S.
- `pathway_coherence_rate` — retrieved → avoided every warned signature → **verified success**.
  This is the PAST-Bench "gain followed the intended pathway" number.
- `success_with_retrieval` vs `success_without_retrieval` — descriptive only (confounded).

## Honest limits

- **ACTIVATE is a proxy.** "Avoided the warned failure" is not proof the hint caused the
  change of behaviour. **Only the ON/OFF ablation (Experiment B) is causal.**
- A run that never retrieves (empty store / low similarity) is not a failure of the
  pathway; report `retrieval_rate` separately so a low rate isn't read as "no benefit".
- Signature classes are coarse by design; a miss is a miss for the wrong reason sometimes.
  Keep the raw `lessons` + step errors in the record for manual audit.

## What this unlocks for the report

T2 becomes three numbers, not one:

```
T1 -> T2 success Δ                 (did it move?)
      + retrieval_rate             (could the lesson reach it?)
      + pathway_coherence_rate     (did the move go through the pathway?)
```

If success rises but coherence is ~0, the gain is **not** from learning — say so.
If success is flat and coherence is ~0, the likely culprit is **activation/following**
(2605.30621) — a worker-capability problem, not a miner problem. That distinction is the
whole point.
