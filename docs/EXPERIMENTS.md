# Experiments: SYSTEM learning, not model training

Read this before interpreting any T1/T2 number. The two are easy to conflate and
the distinction is the whole point of the pipeline.

## What is being learned

```
Learner : the Swarm OS CLI / agent system
Worker  : Qwen (robs4b) — the model that makes individual decisions
```

The self-learning loop changes **the system around Qwen** — tool selection,
memory injection, recovery, routing, semantic cache, verification, critic
behavior, trajectory-derived policy. It does **not** change Qwen's weights.

```
Qwen weights   = UNCHANGED
CLI behavior   = CHANGING
```

## Experiment A — End-to-end behavioral improvement of the self-learning CLI

```
T1 (frozen 60) ──► 400 experiences ──► mine + learn ──► T2 (same frozen 60)
```

- T1 baseline: **41/60 = 68.3 %** (frozen holdout, never trained on)
- Question: **Did the CLI/system get better?**
- NOT a question this answers: "Did Qwen get smarter?" — weights are unchanged.

Precise name for the T1→T2 number:

> **End-to-end behavioral improvement of the self-learning CLI.**

It is the *combined* effect of policy + memory + tool routing + trajectory
learning + recovery + verification. **You cannot attribute the delta to any one
component from T1→T2 alone** — which is exactly why Experiment B exists.

## Experiment B — Memory ablation (does memory actually help?)

```
        SAME tasks (identical ids, model, tools, permissions, temp, verification)
             │
     ┌───────┴────────┐
     ▼                ▼
 MEMORY ON        MEMORY OFF      (SWARM_MEMORY_INJECT=1 / 0)
   50 runs          50 runs
     │                │
     └───────┬────────┘
             ▼
   compare: success · steps · recovery · loops · tool-choice · verification
```

- Question: **Did memory cause part of any improvement?**
- Outcomes are all informative:
  - ON ≫ OFF → memory contributes.
  - ON ≈ OFF → the current memory system isn't contributing (a real finding).
  - OFF > ON → memory is feeding stale/noisy context — the most important finding.
- Harness: `qwen_train/run_memory_ablation.py` (does NOT touch the 400-run data;
  writes its own `qwen_train/results/memory_ablation.json`).
- **Precondition:** the `SWARM_MEMORY_INJECT` gate in
  `runtime_v2/services/stream_runner.py` must exist (3-line change, applied only
  after the 400 finishes — editing repo files during a curriculum run drifts the
  mined line-count/symbol answers).

## Experiment C — Robs-4B QLoRA (only AFTER A/B)

```
best CLI trajectories → GOLD → QLoRA → robs4b v2 → local coding agent
```

- Question: **Can what the CLI learned transfer into your 4B?**
- This is a *separate* comparison: **T2 → T3 = model distillation / QLoRA
  improvement.** Do not report it as part of T1→T2.
- Goal it serves: Robs 4B fixing V-Horseshoe locally without cloud models.

## The three numbers, kept separate

| Experiment | Comparison | Measures |
|---|---|---|
| A | T1 → T2 | system/CLI behavioral improvement (weights unchanged) |
| B | memory ON vs OFF (50+50) | memory's causal contribution |
| C | T2 → T3 | model distillation (QLoRA) improvement |

Never move the goalposts: the frozen 60 stays frozen; T2 is run on the *identical*
60 items as T1, same script, same budget.

---

# North star: does the CLI get better with experience?

The product is the **self-learning, self-healing CLI**. Qwen is a worker; the 4B
is a future *student* of the CLI (Experiment C), not the destination.

**North-star metric = the learning curve**, not "how smart is Qwen":

```
experience 1000 -> success 72%
experience 2000 -> success 76%
experience 3000 -> success 81%
```

Reported by `mine_gold.py` as `learning_curve` (success over consecutive
experience bands). A rising curve is the signal; a flat one is a real finding too.

## Self-healing — hard, measurable definition

Not "the AI tried again". A run **self-heals** when it hits a failure, changes
strategy, and reaches a *verified* success:

```
FAILURE -> detect -> change approach -> execute different action -> verify -> SUCCESS
```

```
self_healing_rate = runs that became verified success / runs that hit a failure
```

Reported as `self_healing`. Per-run, `recovery` = a failure followed by a
**different-tool** success (a retry of the same tool is not healing).

## Self-learning — distinct from self-healing

- **Self-healing**: "I fixed my current mistake."
- **Self-learning**: "I learned something that helps me on a LATER task."

Self-learning is **cross-task** and is therefore not fully measurable within a
single batch — the frozen T1→T2 (Experiment A) and the memory ablation
(Experiment B) are the real transfer tests. Treat any within-batch "improvement"
as a hypothesis, not proof.

## Confound control (mandatory)

An early→late success rise inside one batch can be **task mix**, not learning. The
miner reports both the raw and the **mix-adjusted** (direct-standardized by tool
shape) delta:

```
        RAW early->late delta   vs   MIX-ADJUSTED delta
```

If `raw_delta >> mix_adjusted_delta`, the raw trend is composition. **Do not report
the raw delta as learning.** (First run of this control: raw 0.0 vs adjusted
-0.007 over 88 runs — the earlier +10.7 pp early/late reading was task mix.)

