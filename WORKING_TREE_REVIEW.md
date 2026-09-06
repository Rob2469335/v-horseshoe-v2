# Working-Tree Review Tracker (uncommitted 77-file set)

Captures review findings against the large pre-existing uncommitted set at HEAD
`edb515f` (robs4b rename + broad reformat + healing hardening + qwen V6 recipe).
**Purpose: keep specific, verified findings from being lost before the set is
committed.** Review date 2026-09-06. This is a scratch tracking note, NOT
committed app documentation — reviewed individually before acting.

## Fixed during review (verified, no longer an issue)
- **15 bare `except A, B:` comma-form regressions** (HEAD had `except (A, B):`)
  restored across 10 files — repair_engine.py:1198, system_intel.py:244,
  api_features.py:56&63, sandbox_repl.py:151, recovery_engine.py:75,
  recovery_primitives.py:42&76&120, system_probes.py:150&176,
  system_recovery.py:71, symbol_context.py:37&75, case_tracker.py:98.
  15/15 now match HEAD exactly (hence NOT separately committable — no diff vs
  HEAD remains). Guard test `test_memory_timestamp_except_handlers_are_parenthesized`
  only scans routes/deep_research/approval_registry (3 files), so it never caught
  these in the other 10.
- **Dead eval split in qwen V6 trainers** — `train_v4.py` + `train_v4_cuda.py`
  carved 10% via `random_split` into `eval_dataset` that was never passed to the
  `Trainer` (≈20 rows silently discarded on a small corpus). Removed the carve;
  both now train the full `dataset`. Compile clean.

## OPEN — resolve before or at committing the 77-file set
1. **`checkpointing.py` `delete_checkpoint` manually unlinks the `filelock`
   lock file** — redundant in the good path (filelock 3.32.5 self-removes on
   exit) and can break mutual exclusion if a concurrent `write_checkpoint` for
   the same cid holds it. Durability/concurrency anti-pattern. Fix: drop the
   explicit `unlink`, rely on filelock's release. (~line 106.)
2. **`security_gate.py` strict-mode over-block + coverage — RESOLVED (fixed in
   working tree).** The working-tree diff had widened `visit_Attribute` to flag
   `*.replace`/`*.remove`/`*.rename`/etc. on ANY receiver in strict mode (no
   longer gated on `os`-bound names), false-denying benign duck-typed code like
   `text.replace(...)`/`list.remove(...)`/pathlib renames. Reverted branch 1 to
   receiver-aware scoping (`node.value.id in self._os_names`), restoring the
   documented design (AGENTS.md record + the existing
   `test_security_gate_object_method_named_exec_not_blocked` precedent). Added 4
   regression tests: duck-typed banned-name methods now allowed, os-bound
   receiver still blocked, bare `__builtins__` blocked (new hardening branch),
   constant-dunder `getattr/setattr/delattr` blocked (new hardening branch
   coverage). 28/28 security tests pass. NOTE: `test_repair_records_durable_state_on_success_and_failure`
   hangs alone (pre-existing, unrelated — durable-state test, not security;
   24/25 repair_guards pass).
3. **`swarm_kernel.py` + `selection.py` orphaned `Organism.fitness` — RESOLVED
   (writers restored).** A partial refactor removed both writers of `o.fitness`
   (`selection.py` `o.fitness += composite`; `swarm_kernel._breed_children`'s
   `o.fitness *= self.fitness_decay` decay loop), leaving the field stuck at
   0.0 for consumers (snapshot persistence swarm_kernel:201/352, metrics.py,
   `top.fitness`, `o.memory.write` total_fitness, `__repr__`, restore.py). This
   was NOT dead code: `o.fitness` (raw composite, cross-gen decay = recency-
   weighted) is a different quantity from `genome.average_fitness` (cumulative
   composite/evaluations, monotonic). Decision: keep BOTH quantities (decay
   keeps selection pressure live; a good-once organism can't squat a frozen
   score — the documented elitism-stagnation concern). Restored both writers;
   kept the independent `mutate(child, parent_fitness)` improvement. Added
   tests/test_swarm_kernel.py::test_breed_decays_organism_fitness (REVERT-
   PROOF: fails when decay disabled). NOTE: the async step_async kernel tests
   (test_population_stays_bounded etc.) HANG pre-existing — hangs even without
   the decay loop (isolated), same async-stall category as the durable-state
   test. Sync tests pass.

## OPEN qwen-training context-dependent items (tradeoffs / possible intent — DO NOT auto-revert)
4. **`train_v4.py` (XPU) sets `bf16`** — reopens the documented SPIR-V/bf16
   iGPU crash (AGENTS.md pins XPU loading to fp16). Might be a deliberate
   change (new driver? test?) — READ git history/context around it before
   reverting; don't guess intent.
5. **`train_v4_cuda.py` MAX_LEN 2528→2048** truncates long V6 rows mid-answer;
   contradicts AGENTS.md preserved-answer-tail guidance and the sibling
   `fix_pod.py` (2528). Possible intentional memory-constraint response.
6. **`assemble_chatml_v6.py` TOTAL_TOKEN_CAP 2300→2500** leaves ~28 tokens under
   the 2528 OOM wall; metric only flags >2400. Possible intentional tradeoff.
7. **`train_v4_cuda.py`** prints `"Starting XPU training..."` on a CUDA pod
   (copy-paste; cosmetic).

## Also noted (lower priority, part of the set)
- `evolution_daemon.py` / `tool_executor.py:869` / `assemble_chatml_v6.py:95`
  contain bare-except comma forms that were **added** (HEAD had no except at that
  line) rather than paren-strips — newly-written bare code, not a restore target;
  check against the parenthesized-tuple convention before final commit.
- `bin_vulkan_stable/` is a full duplicate of live `bin/` (known-good Vulkan
  copy; binaries not in git) — do not delete unless a reliable re-download path
  is confirmed.
