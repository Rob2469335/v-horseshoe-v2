# F2-S8 — S8-001 trust root and governed-execution authorization — PROPOSED request

**Status:** PROPOSED — NOT GRANTED — NOT APPROVED. No identifier is assigned.
**Date:** 2026-10-16
**Repository HEAD at writing:** `eb9d6c65d18f51a61181a44053b780801c120a1b` (branch `agent/q9-classification-sota`, PR #33 OPEN, nothing pushed)
**Owner/authority required:** Rob (human operator / experiment authority).

This document prepares the **smallest precise authorizations** needed to produce
S8 base/gold evidence and thereby raise the credited admission count above the verified
**4 of 300**. **It grants nothing. Nothing here was executed.**

It covers three distinct owner actions that are **independent of each other** and must
not be conflated:

- **B — provision the owner-controlled trust root** (§2).
- **D — provision the governed session variables** (§3).
- **C — sign one bounded `S8-001` / `base_gold_production` grant** (§4).

A trust root (B) is not a grant (C); a grant (C) is not env readiness (D); env readiness
(D) is not evidence; and none of them is an admission. **No secret value appears in this
document, and no private key, signature, grant or trust root is created by it.**

---

## 1. Authority trace: existing permission vs proposed permission

| Action | Status | Controlling source |
|---|---|---|
| Authorization-path hardening (strict binding, single-use consumption, trust-root identity) | **EXISTS** | `F2-IMPL-AUTH-033`; code in `qwen_train/f2_authorization.py`, `f2_authz_ledger.py` |
| Execution of up to the 25 phase-2 candidates | **EXISTS (owner, 2026-10-15)** | `OWNER_DECISION_RECORD_2026-10-15.md` Decision 1 — "authorizes execution of up to those 25 eligible tasks" |
| Population acquisition / screening | **EXISTS** | `F2-IMPL-AUTH-028`/`-029`/`-031` |
| S8 runner/evidence hardening | **EXISTS** | `F2-IMPL-AUTH-035` |
| **Trust root provisioned and pinned** | **PROPOSED (§2)** | `f2_authorization.load_trust_root` — "no authorization trust root provisioned… all stages denied" |
| **The 13 governed session values exported for the run** | **PROPOSED (§3)** | `f2_s8_compose.load_governance_from_env`; preflight `env.*` findings |
| **One signed `S8-001` grant** | **PROPOSED (§4)** | `OPERATION_REQUIRED_BINDINGS[("S8-001","base_gold_production")]` |
| Stage-1 build (produces the digest the grant binds) | **PROPOSED separately** | `docs/EXPERIMENT_J_F2_S8_STAGE1_BUILD_PHASE2_AUTHORIZATION_REQUEST.md` (Record A) |
| Egress enforcement / Q9 attestation | **REQUIRES PRIVILEGED HOST ACTION** | preflight `host.egress_enforcement`; not addressed by this document |

**Ordering is not free (from the code).** The grant binds
`candidate_manifest_digest = sha256(<the manifest file the runner reads>)`
(`f2_s8_pilot_live.py:270-271`, bound at `:355`). That manifest is **produced by
Stage-1**. So the owner **cannot sign the grant before the build has run and its manifest
digest is recorded.** Record A is therefore a precondition of Record B's §4, not a
parallel action.

> **Naming note, stated precisely.** The binding *field* is named
> `candidate_manifest_digest`, but the runner binds it to the **Stage-1 build manifest**
> digest, not to the 25-id candidate manifest (`phase2_candidate_manifest_2026-10-15.json`,
> sha256 `a7a51b51…f224131`). The owner should sign the **build manifest** digest.

---

## 2. B — Owner-controlled trust root (owner action; the agent must not create it)

**Location.** `qwen_train/authorization/trust_root.json` (default,
`f2_authorization.DEFAULT_TRUST_ROOT`), or any path exported as
`F2_AUTHORIZATION_TRUST_ROOT`.

> **It must live OUTSIDE the execution worktree** (§6). `qwen_train/authorization/` is
> **not** gitignored; placing the root at the default path inside the executing checkout
> adds an untracked file, makes the tree dirty, and **denies** the protected operation
> by design.

**Exact file format** (`f2_authorization.load_trust_root`):

```json
{
  "signers": {
    "<key_id>": { "public_key": "<base64 of the 32-byte raw Ed25519 public key>", "epoch": 1 }
  },
  "revoked_key_ids": [],
  "revoked_record_ids": []
}
```

- `signers` required and non-empty. Each value may also be the base64 string alone.
  `epoch` must be a non-negative integer.
- The decoded key must be **exactly 32 bytes** (raw Ed25519). PEM/SPKI text, a 33-byte
  leading-zero form, or any other length is refused.
- `revoked_key_ids` / `revoked_record_ids` are the **authoritative post-signing
  revocation source** (there is no external revocation service). JSON with duplicate keys
  is refused.

**Owner procedure (outside this repository's trust path).**

1. Generate an Ed25519 keypair with the owner's own tooling. Keep the private key where
   only the owner can reach it; **never** place it in the repository.
2. Base64-encode the **32-byte raw public key** and place it under a chosen `key_id`.
3. Compute the file digest: `Get-FileHash <trust_root.json> -Algorithm SHA256`.
4. Establish that digest **out-of-band** (not through this process), then export it as
   `F2_AUTHORIZATION_TRUST_ROOT_SHA256` for the run scope.

**Independent fingerprint verification.** Read-only and needs no authorization, but it
**needs a root to describe** — it runs only **after** step 4 above. With the default path:

```powershell
.venv\Scripts\python.exe -c "from qwen_train.f2_authorization import describe_trust_root as d; import json; print(json.dumps(d(), indent=2))"
```

or, for a root outside the default location:

```powershell
.venv\Scripts\python.exe -c "from qwen_train.f2_authorization import describe_trust_root as d; import json,pathlib; print(json.dumps(d(pathlib.Path(r'<path>')), indent=2))"
```

Returns **public material only** — `path`, `digest_sha256`, `signer_key_ids`,
`key_epochs`, `revoked_key_ids`, `revoked_record_ids`. It never returns private key
material (a trust root holds none). Comparing `digest_sha256` against the owner's
out-of-band value is what establishes the pin.

> **Before a root exists, the first command raises**
> `AuthorizationError: no authorization trust root provisioned at <default path>; all
> stages denied` — re-verified 2026-10-16 against the current tree. That exception is the
> fail-closed proof that Gate B is unmet; it is not a tooling failure.
>
> **Correction to `docs/EXPERIMENT_J_F2_S8_OWNER_PREREQUISITE_PACKAGE.md` §4**, which
> presents this command as "Independent verification available **now** (read-only, no
> authorization)". As written it cannot succeed until a root file exists. That document
> is not modified here; the discrepancy is reported.

**What the pin does and does not prove.** `F2_AUTHORIZATION_TRUST_ROOT_SHA256` is a
process-controlled consistency check, **not independent custody**: any principal who can
set the environment can set both the pin and the root path. The only genuine
owner-custody step is the out-of-band comparison above.

**Explicitly not requested here:** the agent must not generate the keypair, write the
trust root, or set the pin. This document asks the owner to do all three.

---

## 3. D — The governed session variables (exact sets, reconciled from code)

**A discrepancy is resolved here.** `OWNER_DECISION_RECORD_2026-10-15.md` and
`PHASE2_LAUNCH_STATUS_2026-10-15.md` §4 name **nine** `SWARM_S8_*` variables;
`docs/EXPERIMENT_J_F2_S8_OWNER_PREREQUISITE_PACKAGE.md` Gate D names **thirteen**. Both
are right about different sets. The machine-checked truth
(`python -m qwen_train.f2_preflight --json`, run 2026-10-16) is: **13 absent**.

### 3.1 The runner-required set (9) — what `f2_s8_pilot_live` needs to run at all

Source: `f2_s8_compose.py:80-88` (`load_governance_from_env`), fallbacks at `:97-103`.

| Variable | Purpose | Fallback | Has fallback? |
|---|---|---|---|
| `SWARM_S8_ARTIFACT_ROOT` | trusted artifact store root (absolute path) | `SWARM_F2_ARTIFACT_ROOT` | yes |
| `SWARM_S8_ARTIFACT_RETENTION_DAYS` | artifact retention in whole days | `SWARM_F2_ARTIFACT_RETENTION_DAYS` | yes |
| `SWARM_S8_EVALUATOR_ID` | authorized evaluator identity | `SWARM_F2_EVALUATOR_ID` | yes |
| `SWARM_S8_EVALUATOR_VERSION` | authorized evaluator version | `SWARM_F2_EVALUATOR_VERSION` | yes |
| `SWARM_S8_EVALUATOR_IMPL` | evaluator implementation artifact path | `SWARM_F2_EVALUATOR_IMPL` | yes |
| `SWARM_S8_EVALUATOR_PROCEDURE` | authorized evaluator procedure identity | `SWARM_F2_EVALUATOR_PROCEDURE` | yes |
| `SWARM_S8_ENVIRONMENT_IDENTITY` | execution environment identity | — | **no — fails closed by name** |
| `SWARM_S8_BASE_STATE_DIGEST` | opaque digest of the base execution state | — | **no — fails closed by name** |
| `SWARM_S8_GOLD_STATE_DIGEST` | opaque digest of the gold execution state | — | **no — fails closed by name** |

### 3.2 The additional readiness set (4) — required by the preflight `env.*` gate

| Variable | Purpose |
|---|---|
| `SWARM_DISTILLER_MODEL` | distiller model identity |
| `SWARM_DISTILLER_WEIGHTS_DIGEST` | sha256 of the distiller weights |
| `SWARM_F2_EMIT_BUNDLE` | must be enabled or **no evidence chain is emitted** |
| `SWARM_F2_TASK_OUTCOME_REPORT` | destination the evaluator writes the report to |

**Union to satisfy both gates: the 13 names of §2 in
`docs/EXPERIMENT_J_F2_S8_OWNER_PREREQUISITE_PACKAGE.md` Gate D** (the six
`SWARM_F2_*` evaluator/artifact names satisfy the runner set through their `SWARM_S8_*`
fallbacks). Additionally, `SWARM_F2_CONVERSION_RECORD` should point at the existing
`…\s8\conversion\conversion_record.json` to clear the provenance gate.

**Already satisfied (do not re-provision):**

| Item | State |
|---|---|
| `SWARM_RECEIPT_KEY` | **PRESENT** via the repository `.env` (allow-listed loader); absent from the raw process env; value never read |
| the 7 child control values | **MATCH** — `env.controls` = 7 of 7 |

**Handling rules (binding).** Export the values **for the run only**; never commit them,
never write them into `.env`, a workspace, a log, or a manifest; never print them. A
value that must be a secret (e.g. a distiller token) is **not** requested here — the
values used in the five-task session are recorded, non-secret, in
`…\s8\pilot_governance_env_used.json` for the operator's reference. Whether the base/gold
**state** digests remain current after the 2026-10-15 VM resource upgrade is an **owner
determination, not an agent inference**.

**Independent verification (name-only; no value is read or printed):**

```powershell
.venv\Scripts\python.exe -m qwen_train.f2_preflight --json
# expect: every env.* finding no longer 'absent'
```

---

## 4. C — The signed `S8-001` / `base_gold_production` grant (owner-signed)

**Artifact:** one file `{"record": {…}, "signature": {…}}`, path exported as
`F2_AUTHORIZATION_RECORD`. Required record fields (`f2_authorization._REQUIRED`):
`id`, `schema`, `stage`, `operations`, `approver`, `not_before`, `not_after`,
`protocol_digest`.

### 4.1 Schema separation and live execution policy (OD-1 & OD-2)

- **Legacy v1 schema (`f2-authorization/v1`):** Verified strictly according to legacy
  rules for historical audit, evidence inspection, and offline replay (`PROVEN IN CURRENT REVISION`).
  Under **Owner Decision OD-2**, live execution strictly **rejects** all v1 authorization records
  fail-closed (`V1_LIVE_EXECUTION_PROHIBITED`). Batch `s8-batch-2026-10-23-b8` is unsigned and
  strictly prohibited from execution (`PROVEN IN CURRENT REVISION`). An allowlist, digest match,
  or prior test cannot substitute for an authenticated owner signature.
- **Current v2 schema (`f2-authorization/v2`):** Under **Owner Decision OD-1**, live execution
  strictly requires `schema = f2-authorization/v2`. In addition to the required v1 fields, v2
  mandates:
  1. An explicit `budget` object declaring integer limits:
     `max_executions` (1..500), `max_total_tasks` (1..5000), `max_retries` (0..5000). Booleans,
     negative numbers, missing keys, or out-of-range values fail closed.
  2. `candidate_pool_digest`: 64-hex SHA-256 bound to the owner-ratified candidate pool.

**Required bindings** (`OPERATION_REQUIRED_BINDINGS[("S8-001","base_gold_production")]`)
— must be present **and** equal the independently obtained value:

| Binding | Expected value comes from |
|---|---|
| `repository_revision` | derived by the verifier from `git rev-parse HEAD` of the **executing checkout** (caller values are compared, never trusted) |
| `candidate_manifest_digest` | **the Stage-1 build manifest digest** (§1 naming note) — recomputed by the runner from the bytes it is about to read |
| `registration_sha256` / `registration_manifest_sha256` | `5f3c9f47eb622d7d983cfdcb6ebd3d240f6936087a049781c741ba37e237f042` |
| `candidate_pool_digest` (v2 only) | recomputed by `v2_preflight` from the owner-signed S8 candidate pool file |

**Reviewable skeleton (v2 record placeholder — no real signature exists):**

```json
{
  "record": {
    "id": "s8-001-phase2-<owner-chosen>",
    "schema": "f2-authorization/v2",
    "stage": "S8-001",
    "operations": ["base_gold_production"],
    "approver": "<owner key identity>",
    "budget": {
      "max_executions": 1,
      "max_total_tasks": 5,
      "max_retries": 0
    },
    "candidate_pool_digest": "<sha256 of the ratified candidate pool>",
    "not_before": "<ISO-8601, tz-aware, e.g. 2026-10-17T00:00:00+00:00>",
    "not_after":  "<ISO-8601, tz-aware>",
    "protocol_digest": "<the frozen protocol digest>",
    "repository_revision": "<the authorized commit sha>",
    "candidate_manifest_digest": "<sha256 of the Stage-1 build manifest>",
    "registration_sha256": "5f3c9f47eb622d7d983cfdcb6ebd3d240f6936087a049781c741ba37e237f042"
  },
  "signature": {
    "alg": "ed25519",
    "key_id": "<a key_id present in the trust root>",
    "key_epoch": 1,
    "signer": "<must equal record.approver>",
    "sig": "<base64 Ed25519 over canonical_bytes(record)>"
  }
}
```

Signature is over `canonical_bytes(record)` = `json.dumps(record, sort_keys=True,
separators=(",",":"), ensure_ascii=False).encode("utf-8")` (`f2_authorization.py:153-157`),
with recursive MappingProxyType unwrapping to ensure deterministic serialization.
`signature.signer` **must equal** `record.approver`, `signature.alg` must be `ed25519`,
and `signature.key_epoch` must equal the trust root's epoch for that key when the root
declares one.

**Validity / epoch / revocation rules.**

- `not_before ≤ now ≤ not_after`, tz-aware; a record outside its window is denied.
- A revoked `key_id` is denied even while still listed; a revoked record `id` (via the
  trust root's `revoked_record_ids`) is denied; a record carrying `revoked: true` or
  `superseded_by` is denied.
- Scope can never exceed the stage's own operation set — for `S8-001` that is exactly
  `{"base_gold_production"}`.

### 4.2 Single-use consumption (historical v1) vs Grant-wide reservation (OD-1 v2)

- **Historical v1 ledger (reconciled against code):** Historical v1 single-use consumption
  is recorded under `C:\Users\rober\F2_AUTHZ\ledger` (default `DEFAULT_LEDGER_ROOT` in
  `qwen_train/f2_authz_ledger.py`), keyed strictly by `<record_digest>.json`. There is no
  `F2_AUTHZ_LEDGER_DIR` environment override in production code; production defaults are
  immutable and fail closed.
- **Grant-wide v2 reservation (`f2_authz_budget.py`):** Live v2 execution uses atomic budget
  reservation (`verify_and_reserve`). It coordinates under a persistent store-wide transaction
  lock (`.transaction.lock`), pre-claims execution lock files in `exec/`, writes sequential
  attributable task records in `tasks/`, and commits the slot atomically via `O_CREAT | O_EXCL`
  in `<seq:06d>.json`.
- **12-state conservative crash accounting:** Any incomplete reservation, orphan task, malformed
  slot, or uncommitted retry is detected during audit and halts further allocation (`RECONCILIATION_REQUIRED`).
  Crashes NEVER result in automatic refunds (`PROVEN IN CURRENT REVISION`).
- **Production budget root:** `PRODUCTION_BUDGET_ROOT` defaults to `None` in production code and
  strictly fails closed until an explicit root is provisioned by owner authority.

**A binding constraint the owner should know.** The signed-record binding vocabulary
(`BINDING_FIELDS`) has **no field for the base/gold state digests**. Those are carried by
the governed environment (`SWARM_S8_BASE_STATE_DIGEST` / `SWARM_S8_GOLD_STATE_DIGEST`,
§3.1) and written into each per-task evidence record. **Binding them into the signed
record as well would require a code change to `BINDING_FIELDS`, which is
`REQUIRES AUTHORIZATION` and is not proposed here.**

---

## 5. Pre-consumption checks (ordered; all host-side, all fail closed)

> **2026-10-24 correction.** The `verify_and_consume` steps below are historical
> and are superseded for live execution by §11 and by OD-2. The current v2 order is
> `v2_preflight` → execution_id + intent → `verify_and_reserve` → audit →
> revocation re-check → tasks. Live v1 remains prohibited. This note preserves the
> historical text rather than deleting it.

Every check below runs **before** `verify_and_consume`, in this order
(`f2_s8_pilot_live.py:248-358`):

1. **Gates supplied and satisfied.** `--gates` must contain all eight keys
   (`authority_current`, `d4_budget_approved`, `d4_storage_cap_approved`,
   `approved_subnet`, `d5_credential_available`, `evaluator_identity_named`,
   `isolation_verified`, `pilot_authorized`) and every one must be true.
   `pilot_gates_phase2.json` currently provides all eight as true.
2. **Manifest read exactly once** and hashed; that digest is the one bound (`:270-271`).
3. **Specs resolve** through the source adapter (canonical commands, §3 of Record A).
4. **`--only` validated.** An unknown id refuses and states no grant was consumed.
5. **Zero-runnable refused.** A manifest resolving to zero specs fails rather than quietly
   succeeding.
6. **Every selected `status="built"` entry supplies a non-empty `workspace`** — refused
   otherwise, before consumption.
7. **Governed environment loads** (`load_governance_from_env`) — the §3.1 names must
   resolve, else it raises before consumption.
8. **Then, and only then, `verify_and_consume`** with binding
   `{candidate_manifest_digest, registration_sha256}` against the trust root.

**Clean repository identity and expected revision.** A protected operation is **denied on
a dirty working tree** — `_check_binding` (`f2_authorization.py:599-604`) refuses a
non-empty `git status --porcelain` in the executing checkout, because the code cannot be
proven to equal the bound commit. The current working tree holds uncommitted changes and
untracked files that must be preserved, so **no protected operation may run from it**
(§6).

**Inputs and evidence currency.** The runner verifies the manifest and the governed env;
it does **not** re-verify the 75 input files (`f2_evidence.verify_task_evidence` verifies
*retained evidence*, not inputs). Input integrity is therefore a **staging precondition**
(§7 step 1), not a runtime gate — and this document states that limitation rather than
implying a check that does not exist.

**Isolation readiness and egress controls.** `isolation_verified` and
`approved_subnet` are asserted through the gates file; `host.egress_enforcement` is a
**privileged host control** that this repository observes and verifies but cannot impose
(preflight finding). It remains `REQUIRES PRIVILEGED HOST ACTION` and is **not** satisfied
by this document.

---

## 6. Execution environment: the clean worktree (mandatory)

Because a dirty tree denies the operation, the accepted execution strategy is a
**separate, clean git worktree at the authorized revision**:

```powershell
git worktree add --detach C:\f2_exec\rev-eb9d6c65 eb9d6c65
```

- `repository_identity` resolves a worktree's own HEAD (pinned by
  `tests/test_f2_authz_binding_and_replay.py::test_repository_identity_worktree_shares_head`).
- The trust root and the authorization record **must be outside the worktree**
  (absolute `F2_AUTHORIZATION_TRUST_ROOT` / `F2_AUTHORIZATION_RECORD`), or the tree
  becomes dirty and the operation is denied.
- Build outputs (`C:\f2_pilot`) and the manifest are outside the worktree and cannot
  dirty it. The authorization ledger default sits under the gitignored `data/`.
- **The owner chooses the revision.** `eb9d6c65` carries the AUTH-035 hardening (and the
  focused `tests/ -k f2` slice is green against it); `c2bfa856` predates it.
- The existing working tree is **not** to be committed, stashed, cleaned, or reset, and
  no such action is proposed.

---

## 7. The stage-2 invocation, and the small first phase

**Stage-2 command** (from `PHASE2_LAUNCH_STATUS_2026-10-15.md` §5), with the first phase
narrowed by `--only`:

```powershell
.\.venv\Scripts\python.exe -m qwen_train.f2_s8_pilot_live `
    --manifest "C:\Users\rober\F2_EVIDENCE\s8\pilot_build_phase2\build_manifest.json" `
    --inputs   "C:\Users\rober\F2_EVIDENCE\s8\pilot_inputs_phase2" `
    --registration-id "evaluator_compat_python_confirmed_v1" `
    --registration-sha256 "5f3c9f47eb622d7d983cfdcb6ebd3d240f6936087a049781c741ba37e237f042" `
    --ledger "C:\Users\rober\F2_EVIDENCE\s8\trusted_store\s8_pilot_ledger.jsonl" `
    --staging "C:\Users\rober\F2_EVIDENCE\s8\pilot_staging_phase2" `
    --out "C:\Users\rober\F2_EVIDENCE\s8\pilot_results_phase2" `
    --vm "F2-S8-VM1" `
    --credential "C:\Users\rober\F2_EVIDENCE\s8_cred.xml" `
    --gates "C:\Users\rober\F2_EVIDENCE\s8\pilot_gates_phase2.json" `
    --only <a small, owner-named subset>
```

**Make the first authorized execution small and verifiable.** The recommended first
phase is **`--only` on a small owner-named subset (order 3 tasks)**, drawn from the
`built` entries, **after** this staged sequence:

1. Re-verify the 75 input files against their manifests and the aggregate pin.
2. Confirm each selected workspace is complete (`repo`, `repo_gold`, `.venv`,
   `.venv_gold`) and its `base_commit` matches the registration.
3. Confirm the trust root pin, the grant, the 13 env values, the eight gates, and the
   egress control are all in place.
4. Run stage 2 for the subset only.
5. **Evidence review before any further batch.** Inspect the produced ledger rows and
   retained evidence: `outcome ∈ {base_fail_gold_pass, gold_fail, infrastructure_failure}`,
   `outcome_class`, and — for an admitted pair — a canonical per-task `EvidenceRecord`
   verified by `f2_evidence.verify_task_evidence` (retained evaluator report **and a real
   run log** with digests and sizes, `environment_identity`, the base/gold
   `execution_state_digest`, evaluator identity + implementation digest, repository
   revision + registration digest, all bound into the ledger row).
6. Only after that review does the owner authorize the remaining candidates.

> **A realistic yield expectation.** 25 candidates cannot close a 296 gap. Even a fully
> successful phase yields **at most +25** (→ 29/300), and the first small subset yields
> fewer. `plan_run` over the full registered universe finds **3,822 runnable** tasks, so
> there is **no shortage of candidates** — the constraint is authorization to execute,
> at 25 per phase and an 8-hour budget. Reaching 300 requires roughly **12** such phases,
> or a larger owner-authorized `max_tasks_per_run`.

---

## 8. Explicitly prohibited by this document

- Creating, signing, or consuming any trust root, key, signature, grant or attestation
  **by the agent**.
- Provisioning any variable, credential, or secret by the agent; committing any secret.
- Any guest/VM start, attach, or reconfiguration; any firewall/ACL/route/NAT change.
- Running any model, Q10, Q12, Q13, or a T/X arm.
- Committing, pushing, or staging; merging PR #33; modifying `data/` production state.
- Marking any task admitted, CLEAN, or the population READY; weakening, skipping or
  deleting any gate.
- Substituting or fabricating evidence, or back-filling a missing historical field.

---

## 9. Approval requirement, and the first owner decision

**Approvals required (three, independent):**

- **B** — provision and pin the trust root, and verify the fingerprint out-of-band (§2).
- **D** — export the 13 governed values for the run scope only (§3).
- **C** — sign one bounded `S8-001` grant (§4), *after* Record A's build has produced and
  recorded the manifest digest.

**Also required, outside this document:** the egress-enforcement privileged host action,
and Record A (the Stage-1 build grant).

**The first owner decision needed is two-part and can be taken now, before anything is
executed:**

1. **Grant Record A with plan P1 or P2** (the build, and how
   `stanfordnlp__dspy-8739`'s existing 826 MB workspace is handled).
2. **Approve starting B and D now** — they are independent of the build and of each
   other, and B is the precondition for any signed grant.

Nothing is signed, provisioned, built, or run until the owner decides. **This document
must not be marked approved by an agent.**

---

## 10. Explicit non-claims and evidence standards

- This is a **request**, not an authorization; it is not approved, and no identifier is
  assigned.
- No trust root, key, signature, grant, variable, or evidence artifact was created; no
  secret value was read, printed, or written.
- The reference values in `pilot_governance_env_used.json` are the *five-task session's*
  values; whether they remain current is an owner determination.
- A provisioned trust root (B) is not a grant (C); a signed grant (C) does not
  demonstrate isolation; env readiness (D) is not authorization; and no step here is an
  admission or a scientific result.
- **Trust custody:** Exporting `F2_AUTHORIZATION_TRUST_ROOT_SHA256` is `SUPPORTED` as a process
  consistency check, but independent cryptographic custody is `NOT ESTABLISHED` solely by
  environment variables. Out-of-band fingerprint verification by the owner is required.
- **POP-001 derivation:** Registration artifact digest reproducibility is `SUPPORTED`, but
  complete raw-to-registered algorithmic derivation is `NOT ESTABLISHED`. Full derivation
  execution requires dedicated resources and `REQUIRES AUTHORIZATION`.
- **Budget enforcement:** `PRODUCTION_BUDGET_ROOT` defaults to `None` and fails closed
  (`PROVEN IN CURRENT REVISION`).
- **Legacy v1 separation (OD-2):** Historical v1 verification passes for audit/replay
  (`PROVEN IN CURRENT REVISION`), but live execution is prohibited fail-closed (`PROVEN IN CURRENT REVISION`).
- **Batch b8:** `s8-batch-2026-10-23-b8` is unsigned and strictly prohibited from live execution
  (`PROVEN IN CURRENT REVISION`).

---

## 11. 2026-10-24 — S8 standing-authorization v2 correction (UNCOMMITTED, TEST-ONLY)

**Status:** working-tree implementation correction; **nothing is signed, activated, or
switched on**. `PRODUCTION_BUDGET_ROOT` remains `None`. This section records what the
code now does, not a new authorization. It does not ratify `F2-LIFECYCLE` or any marker
protocol.

### 11.1 v1/v2 dispatch

- `f2-authorization/v2` → `v2_preflight` (real registration and pool bytes, binding,
  manifest membership, eligibility) → execution_id + intent → `verify_and_reserve` →
  audit → revocation re-check → tasks.
- `f2-authorization/v1` is **historical verification only**. Live v1 execution remains
  refused under OD-2 (`V1_LIVE_EXECUTION_PROHIBITED`); this correction did not enable it.
- Any unknown schema is refused. A v2 grant never falls through to a v1 path, and v2
  never calls `verify_and_consume`.

### 11.2 v2 grant and pool schemas

A v2 grant carries `schema="f2-authorization/v2"`, `stage="S8-001"`,
`operations=["base_gold_production"]`, a required `budget` object, `target_credited`
(integer >= 300, no default), `candidate_pool_digest`, `registration_sha256`,
`registration_manifest_sha256`, and (when the grant is batch-scoped)
`candidate_manifest_digest`.

The candidate pool carries `schema="f2-s8-candidate-pool/v1"`, sorted unique `task_ids`,
`registration_sha256`, `excluded`, and `provenance`. Pool serialization is canonical:
sorted keys, compact separators, UTF-8, no BOM; a file that is not byte-identical to its
canonical form is refused. A task may not appear in both `task_ids` and `excluded`.

### 11.3 Two registration digests — never interchangeable

- `registration_sha256` is the **derivation/content digest** declared by the registration
  provenance artifact.
- `registration_manifest_sha256` is the **SHA-256 of the real registration file bytes**.
- `candidate_pool_digest` is the SHA-256 of the canonical pool bytes.
- `candidate_manifest_digest` is the SHA-256 of the per-batch manifest bytes.

The pool's `provenance.registration_file_sha256` must equal the real file-byte digest.
The verifier checks the signed grant, the provenance content digest, and the real bytes as
three separate links; one digest cannot substitute for another.

### 11.4 Budget units, limits, allowlist

Units: **execution** = one protected runner invocation; **reservation** = durable claim
before the invocation; **task unit** = one candidate in that execution's manifest;
**attempt** = one actual run of a task; **retry** = an additional attempt after the first.
`max_executions`, `max_total_tasks`, and `max_retries` are all required with no defaults;
their caps are enforced while the transaction is locked. A refused reservation consumes
zero units. A handled write failure rolls back every unit it created; a stuck reservation
guard is left in place for the owner and is never silently resumed.

`target_credited` is required and must be an integer >= 300. Before every reservation the
runner runs the read-only cross-chain reconciliation; if credited >= `target_credited` it
refuses with `TARGET_REACHED` and creates no claim files.

The v2 store allowlist: `exec/`, `tasks/`, `retries/`, `audit/`, the persistent
`.transaction.lock`, and committed numeric slots/task/retry records. No terminal-marker
files are active. Unknown files/directories, renamed slots, mangled slots, and stuck
reservation guards fail closed; renaming a slot never frees capacity.

### 11.5 Concurrency and crash cases

A persistent `.transaction.lock` serializes writers; a `mkdir`-style `.reservation.lock`
is the atomic reservation guard. The runner writes an intent file **before** any claim and
refuses to reuse an existing intent or to generate a new execution ID to bypass a stuck
claim. The execution ID is `SHA-256(grant_digest | manifest_digest | nonce)`.

Documented crash windows: before the claim (intent may exist; owner decides), after the
execution-ID claim before the slot (guard + partial state fail closed), after the
reservation before the first task (reservation remains consumed; no auto-refund), during
a task, and after a task before its result is saved. No crash path refunds; the owner
resolves reconciliation.

### 11.6 Revocation window and audit binding

Authorization is checked before reservation, and re-checked immediately before the first
task. Revocation is **trust-root only**: `revoked_key_ids` / `revoked_record_ids` at grant
verification and again at the pre-first-task recheck. There is no store-marker revocation
check. If revocation is detected after reservation, no task runs and the reservation
remains consumed. A time-of-check/time-of-use window remains between the recheck and the
first task start; revocation after the first task starts does **not** stop that batch.

**2026-10-24 correction:** the former budget-store `REVOKED` marker fast path is removed;
this text is the current trust-root-only contract.

The audit record binds: grant schema, grant digest, registration content digest,
registration file-byte digest, candidate-pool digest, manifest digest, execution ID,
reservation ID, validated task IDs, and executed task IDs.

### 11.7 Tamper, rollback, junction, and custody limits

The production root is the code constant `C:\Users\rober\F2_AUTHZ\budget_v2`, authorized
by the owner on 2026-10-24 and outside `C:\Users\rober\F2_AUTHZ\ledger`. The folder is not
created by this repository; with no signed grant, nothing can use it. A production-style
invocation refuses a temp production root and a temp/test trust root, and otherwise fails
closed.
Symlinks and Windows junctions are refused on the budget root and its parents. That is a
check-before-use guard: a same-user actor can still replace a path component between check
and open (CWE-367). The store has no independent tamper or rollback resistance; signatures
prove authorship/integrity, not freshness or scientific truth. In this working tree the
owner key is `FullControl` for the runtime account, so **key custody / separation of duties
is a `GOVERNANCE GAP`**, not an established control.

### 11.8 Owner decisions still open

Production pool contents; budget numbers; validity period; key custody; build/staging
authority; commit permission. The 185 policy is an owner decision dated **2026-10-24:
EXCLUDE-185**; §16.10 of `F2_S8_EXECUTION_AND_PILOT_PACKAGE.md` retains the earlier
INCLUDED history. The scratch draft and final pools exclude the 185; production policy is
unchanged unless a committed authority document ratifies the exclusion.

### 11.9 Lifecycle quarantine (2026-10-24)

`F2-LIFECYCLE` stage/operations, the marker/completion functions, marker classification,
and the 14 lifecycle tests were removed from the working tree. This is a reversible
quarantine, not a history rewrite. Restore requires an authority document.

- Patch: `C:\Users\rober\F2_SCRATCH\quarantine\20261024_180644\lifecycle.patch`
- Patch SHA-256: `ee3485d56447e106871396c98086758c637631fef7d335db7ffae502c87e143f`
- Test copy: `C:\Users\rober\F2_SCRATCH\quarantine\20261024_180644\test_f2_experiment_lifetime_authorization.py`
- The patch is a removal diff; restore only with `git apply -R -p1` under authority.
- No store-marker revocation check exists. Revocation is trust-root only (grant
  verification and the pre-first-task recheck). Revocation after the first task starts
  does not stop that batch; the residual recheck-to-first-task window is documented in §11.6.

### 11.10 Final COMPATIBLE pool and feasibility (2026-10-24)

The COMPATIBLE-only final pool is **25 tasks**, SHA-256
`cb0a4f8fa77442809820d87793e30b5c99bc29b83ee3c94e37a96b0920d38efc`; it has **0 built
workspaces**. With `target_credited=300`, **POOL CANNOT REACH 300**. The driver is
implemented and tested with a fake runner; a real run stops `NO_ELIGIBLE_BUILT_TASK`.
No grant is signed. Widening the pool requires an owner decision: resolve UNKNOWN tasks by
an authorized build probe, or include the 185 conflict rows, which the owner has excluded.

---

## 12. Build-probe grant request (2026-10-24) — REQUIRES AUTHORIZATION

**Status: PROPOSED — UNSIGNED — NOT AUTHORIZED — NO CODE MAY ACTIVATE IT.** This is a
separate, bounded authorization; it does not modify the execution grant, VM isolation,
network rules, or evidence capture.

### 12.1 Purpose and scope

Resolve the **3,569 UNKNOWN** draft-pool tasks and build the compatible ones. Nothing else
is in scope. The probe pool is exactly the 3,569 sorted UNKNOWN task IDs; its canonical
SHA-256 is `375e6618d8249e279ee0b946fd4797af00e59d48ba7ac2d661e109578c794bf5`.

Proposed bounds (owner must ratify): **1 build attempt per task**, total task cap
**3,569**, per-task time limit **3,600 s** (the builder's existing `run` timeout at
`qwen_train/f2_s8_pilot_build.py:491`; pip subprocesses use 1,800–3,600 s), validity
**30 days from signing**, staged batches of **50**. The first 50-task batch must report
its COMPATIBLE/INCOMPATIBLE/UNKNOWN fraction before any larger batch is authorized.

### 12.2 Network allowlist (derived from code)

- Git clones: `https://github.com/<owner>/<name>.git` only, from
  `qwen_train/f2_s8_acquisition.py:473-480` (`repo_url_for`) and the explicit pilot
  mapping at `qwen_train/f2_s8_pilot_build.py:98-103`; clone at `:942`.
- Python packages: the build invokes `pip install` and `uv sync` without an explicit
  index/allowlist (`f2_s8_pilot_build.py:1038-1041`, `:1095-1115`, `:1179-1180`).
  Those tools default to PyPI (`pypi.org` / `files.pythonhosted.org`) and may fetch
  arbitrary dependency metadata/artifacts. **The repository does not contain an
  exhaustive host allowlist.** A probe grant therefore cannot truthfully promise a
  closed allowlist from existing code; it needs an explicit egress proxy/allowlist or an
  offline wheelhouse. No wheelhouse exists at `C:\f2_tooling`; the only local tool is
  `C:\f2_pilot\tools\uv.exe`.

### 12.3 Isolation and execution boundary

Builds run **host-side** (`C:\f2_pilot`, builder venvs, git/pip/uv) exactly as
`f2_s8_pilot_build.py` does. The Stage-2 guest remains offline with `UV_OFFLINE=1`,
`UV_FIND_LINKS=WHEELHOUSE`, no default gateway/DNS, and unchanged evidence capture. The
probe changes no VM isolation, network rule, or evidence semantics.

### 12.4 Outputs

- A classification evidence JSONL/JSON file per task: `task_id`, `classification`
  (`OBSERVED_COMPATIBLE` / `OBSERVED_INCOMPATIBLE` / `UNKNOWN`), reasons, declared and
  installed plugins, evidence digests, build attempt outcome, and builder error class.
- Built workspace paths under `C:\f2_pilot\<task_id>`.
- Build failures are recorded as **infrastructure**, never as credits or scientific
  outcomes. No admission/credit ledger rows are written by the probe.

### 12.5 Relation to F2-IMPL-AUTH-036

`F2-IMPL-AUTH-036` authorized **one** Stage-1 host build over **24** named tasks with
network access. This probe is a separate, larger (3,569-task) authorization and does not
inherit `-036`; it requires its own owner-signed record, scope, caps, and validity.

### 12.6 Revocation, expiry, caps, automatic halts

Revocation is trust-root only (`revoked_record_ids`), checked before every probe batch and
again immediately before a task build. Expiry is checked before each batch. Caps:
per-task attempts, total tasks, per-task time, and batch size. Automatic halts:
`PROBE_REVOKED`, `PROBE_EXPIRED`, `PROBE_CAPS_EXHAUSTED`, `PROBE_STUCK`,
`PROBE_INFRA_FAILURE_RATE` (>10% in a batch), `PROBE_NO_ELIGIBLE_TASK`,
`PROBE_AUTHORIZATION_REFUSED`. No retry around a refusal; no new ID to bypass a stuck
claim. After 50 tasks, stop and report before a larger batch.

---

## 13. Disk guard and cleanup (2026-10-24) — IMPLEMENTED, NOT ACTIVATED

**Measured (read-only, 2026-10-24):** `C:` free **93.8 GB** / total **475.8 GB**. Built
workspace `stanfordnlp__dspy-8739` = **150,949,704 B (0.141 GB)**; one larger prior
workspace (`networkx__networkx-8196`) = **721,053,078 B (0.721 GB)**; `C:\f2_pilot\build_logs`
= **630,347 B / 123 files**; `C:\f2_pilot\tools\uv.exe` = **44,932,608 B**; **no host
wheelhouse exists** (`C:\f2_tooling` absent). Generalizing one workspace to all tasks is
**NOT ESTABLISHED**; dependency caches/downloads were not measured.

Worst-case totals at the measured 0.141 GB/task: 25 = **3.5 GB**, 300 = **42.3 GB**,
1,500 = **211.5 GB**. At the observed 0.721 GB/task: 25 = **18.0 GB**, 300 = **216 GB**,
1,500 = **1.08 TB**. The 1,500 case exceeds current free space.

**Disk guard:** both drivers require `disk_floor_bytes` and `per_task_disk_bytes` (no
default) and halt `DISK_LOW`/`PROBE_DISK_LOW` before a batch if free space is below
`max(disk_floor_bytes, 3 × per_task_disk_bytes × batch_size)`. Proposed production values:
floor **25 GB**, per-task **141 MB**, batch 50 → threshold **25 GB** (the floor wins;
3×141 MB×50 = 21.15 GB). Tests cover low/high and no partial batch starts.

**Cleanup rules (`f2_s8_cleanup.py`):** dry-run by default; real deletion requires an
explicit `allow_delete` argument. Only allowlisted task workspace/venv/run-temp roots are
eligible; real paths are resolved, symlinks/junctions and allowlist escapes are refused,
and the task-directory name must match the task pattern. Deletion is allowed only when the
task outcome is in the ledger, evidence files exist, were copied under the evidence root,
and SHA-256 match, and the task is not retry-pending/stuck. A JSONL deletion manifest
records path, bytes, reason, and UTC time; it is never deleted. Read-only files are
retried with the write bit; files held open are skipped and logged, never by killing a
process. Failed/infrastructure builds may drop the partial workspace but `.log` files are
retained. A per-run temp sweep removes only old `run-*` directories under the allowlist.
**Do not delete:** authorization ledger, budget root, grants, trust root, keys, `.env`,
evidence/results, pool/registration, wheelhouse/cache, F2_SCRATCH backups/quarantine, the
repository, or anything outside the allowlist.

**Driver integration:** the execution driver supports a tested `cleanup_after_batch`
callback; verified workspaces are removed, unverified workspaces are kept with a warning,
and a subsequent `DISK_LOW` halts. **The real CLI does not activate cleanup**: it requires
explicit evidence-root/manifest wiring, and no real deletion has run. Limits: a bug or a
same-user process can still delete things; the manifest records deletions but does not
prevent them. Nothing in this section is signed or activated.
