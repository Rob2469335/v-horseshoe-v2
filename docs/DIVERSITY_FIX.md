# Fixing the diversity / learning-signal problem

## The diagnosis (from the 400-run)

The pools were never 3,400 *diverse* items — they are **6 families, ~90% single-tool**:

```
1957  sandbox_repl            (no choice)
1196  filesystem             (no choice)
 184  filesystem|lsp|semantic_search   ← the only real tool-choice family
 180  git
   2  system · 1 filesystem|sandbox_repl
difficulty 1→1322 · 2→2009 · 3→189
```

Consequences in the run (`mine_gold.py`):
- **250 of ~320 runs dropped** by the per-shape diversity cap — near-identical shapes.
- **~86% baseline success** — almost no headroom.
- **mix-adjusted learning trend ≈ -0.014** — flat; the CLI shows no within-batch learning.
- GOLD yield only ~8% of successes (24 candidates).

The bottleneck is **task-mix diversity and baseline difficulty**, not run count. More
runs of the same shapes will not produce a learning curve.

## The fix — three parts

### 1. Balance what we have — `qwen_train/diverse_select.py`
Cap each family (default 150), keep every rare family, dedupe prompts, prefer harder
items, and **round-robin interleave** so consecutive runs alternate families.
→ 3,520 → **603 balanced items** (150 each for the 4 main families).

### 2. Generate genuine tool-CHOICE families — `qwen_train/gen_choice_tasks.py`
Balancing can't invent choice; only `filesystem|lsp|semantic_search` had it. This adds
**402 items across 4 new multi-tool families**, each with an exact machine verifier:

| family | target_tools | verifier |
|---|---|---|
| line_count | filesystem · sandbox_repl · system | exact int |
| token_count | filesystem · sandbox_repl · semantic_search | exact int (whole-word) |
| referencing | filesystem · lsp · semantic_search | contains a real referencing file |
| multi_def | filesystem · lsp · semantic_search | yes/no |
| importers | filesystem · lsp · semantic_search | contains a real importer |
| longest_of | filesystem · sandbox_repl | the longest file |

Ground truth is AST/whole-word computed (not guessed), so the verifier is trustworthy
and the "choice" is real (2–3 viable routes per task).

### 3. Wire it (post-400, one small change)
`run_curriculum.load_items()` currently reads `tool_curriculum.jsonl`, `generated.jsonl`,
`holdout.jsonl`. Add `choice.jsonl` + (optionally) `diverse.jsonl` — or a `--pool` flag —
so the balanced/new families can actually be run. **Not applied during the 400** (editing
`run_curriculum.py` mid-run drifts mined answers).

## What "fixed" looks like (measure it)

Re-run on the balanced+choice set and expect, in `mine_gold.py`:
- **baseline success down** (real ambiguity, lower headroom → room to learn);
- **per-shape diversity-cap drops down** (fewer near-identical shapes → higher gold yield);
- **a rising learning curve** and/or a **mix-adjusted delta > 0**;
- then **Experiment A** with **paired significance** (`self_learning_bench.py`: McNemar +
  pre-registered min effect) on the frozen 60 — the honest verdict.

If the curve is still flat after real diversity, the finding is "the current policy/memory
does not change decisions" — and that is a *worker/activation* result (arXiv:2605.30621),
not a data-quantity one.
