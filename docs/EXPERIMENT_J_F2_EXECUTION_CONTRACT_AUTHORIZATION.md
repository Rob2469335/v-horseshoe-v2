# F2 Execution/Import Contract Authorization: Fresh-Process Repository-Root Bootstrap

## Purpose

Authorize a scoped implementation that defines and enforces the F2 fresh-process
execution/import contract: every F2 fresh Python child process must explicitly
place the repository root (`REPO_ROOT`) at the front of `sys.path` using
`sys.path.insert(0, REPO_ROOT)` **before** importing repository packages, then
independently load and verify its serialized F2 freeze manifest/artifact.

This authorization records the operator-selected mechanism. It does not reopen
the mechanism decision. It does not amend F0 or F1.

## Human Operator Decision

> Every F2 fresh Python child process must explicitly place `REPO_ROOT` at the
> front of `sys.path` using `sys.path.insert(0, REPO_ROOT)` before importing
> repository packages. The child must then independently load and verify its
> serialized F2 freeze manifest/artifact.

The following are NOT substitutions for the authorized mechanism:
- inherited `PYTHONPATH`;
- editable-install discovery;
- reliance on the current working directory;
- implicit site-package discovery;
- an alternative launcher/import mechanism.

## Mechanism

`sys.path.insert(0, REPO_ROOT)` before repository imports in every F2 fresh child
process, equivalent in principle to the proven F1 fresh-runtime bootstrap
(`qwen_train/f1_infra.py` `sys.path.insert(0, r'...REPO_ROOT...')`).

## Required Behavior

1. Fresh Python process (separate PID / interpreter).
2. Explicit repository-root bootstrap (`sys.path.insert(0, REPO_ROOT)`) before repository imports.
3. Repository-source imports (not editable-install visibility).
4. Child-side manifest loading.
5. Child-side independent manifest verification.
6. Fail-closed behavior preserved.
7. Existing replay transport preserved.
8. No second, competing replay/delivery mechanism introduced.

## Implementation Scope (authorized files/classes)

### IN SCOPE

- `tests/test_f2_replay.py`
  - `_run_child()` — single enforcement point for all F2 fresh child launches;
    prepends the authorized `sys.path.insert(0, REPO_ROOT)` bootstrap to every
    child script before it is executed.
- The three F2 child-script bodies (via `_run_child`), which already perform
  child-side load + verify and must remain unchanged in their manifest logic.
- `docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md` (this artifact).
- `docs/LEARNING_EXPERIMENT_STATE.md` — ONLY the F2 checkpoint (§10) information
  directly affected by this authorized work (see State-Document Scope).

### OUT OF SCOPE (NOT authorized to change)

- `docs/EXPERIMENT_J.md` (F0, frozen).
- `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` (F1 parameters unchanged).
- `runtime_v2/services/f2_freeze.py`, `runtime_v2/services/f2_replay.py`
  (implementation already correct; unchanged).
- `swarm_os/services/f2_freeze.py`, `swarm_os/services/f2_replay.py` (wrappers; unchanged).
- `swarm_os/app/main.py`, `start-dev.ps1`, `pyproject.toml`, `.gitignore` (unchanged).
- All other tests except `tests/test_f2_replay.py` (unchanged).
- Packaging, dependencies, editable installation (NOT to be "fixed").

## Verification Scope (acceptance conditions)

1. Parent process can run the F2 test harness.
2. Fresh child process can import `runtime_v2` from the repository source tree.
3. Fresh child can load the persisted manifest.
4. Fresh child independently verifies the manifest/artifact.
5. The three existing child-process failures caused by
   `ModuleNotFoundError: No module named 'runtime_v2'` are resolved.
6. Existing F2 freeze/replay tests remain passing.
7. No test depends on the stale editable-install finder.
8. No F2 test silently falls back to live retrieval.

Evidence of a green parent pytest invocation alone is NOT sufficient proof of
fresh-process correctness; the child path must be demonstrated separately.

## Boundaries

- F0 remains frozen.
- F1 scientific parameters remain unchanged.
- F1 authorization remains unchanged.
- No scientific endpoint changes.
- No treatment-definition changes.
- No contamination-rule changes.
- No promotion changes.

## Explicit Exclusions (NOT AUTHORIZED)

- `SWARM_RECEIPT_KEY` provisioning.
- Genuine ACTIVE lesson creation.
- F2 treatment freeze for real experiment data.
- Experiment J observations (T/X/C0).
- Evaluation / promotion.
- F2 certification.
- N=2.
- Unrelated cleanup / refactoring / test repair.
- Broad packaging changes.
- Dependency additions.

## State-Document Scope

Authorize ONLY the minimal update to `docs/LEARNING_EXPERIMENT_STATE.md` §10
necessary to accurately record that this authorized F2 fresh-process
execution/import contract has been implemented and verified. The update must
preserve the distinct statuses of: authorization, implementation, verification,
certification, ACTIVE lesson, treatment freeze, experiment readiness, and N=2
authorization. It must NOT mark F2 experimentally complete, certified, or N=2
authorized.

## Authorization

**AUTHORIZED** for implementation and verification of the F2 fresh-process
execution/import contract only, per the scope and boundaries above.

**NOT AUTHORIZED** for:
- Running Experiment J, T/X/C0 observations, or N=2.
- Evaluation or promotion.
- `SWARM_RECEIPT_KEY` provisioning or ACTIVE lesson creation.
- Any change beyond the scope defined above.

---

*Authorized: 2026-09-29*
*Operator: Rob (human operator)*
*Scope: F2 fresh-process execution/import contract — repository-root sys.path bootstrap only*
*Related: F1-OP-INFRA-001, F1-OP-INFRA-002, F1-OP-INFRA-003 (F1 infra precedence pattern)*