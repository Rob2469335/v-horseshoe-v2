# F2-S8 — Execution revision, isolated checkout, and Stage-1 build preparation

**Status:** PREPARATION RECORD — grants nothing, executes nothing, commits nothing.
**Date:** 2026-10-16
**Repository HEAD:** `c2bfa85655b8ced357500a6427c27c657c7a7c0e` (branch
`agent/q9-classification-sota`; PR #33 OPEN, unmerged; `origin/…` == HEAD)
**Owner decision required:** exactly one (§1.6). Everything else here is
established by repository evidence.

This record answers the four preparation questions (execution revision, isolated
checkout, Stage-1 build invocation, owner prerequisites) from source inspection.
It is not a readiness audit: the readiness authority remains
`python -m qwen_train.f2_preflight`.

---

## 0. Corrections to the re-used results (evidence, not assumption)

Three items from the inherited result set do not survive direct inspection. They
are recorded here because §1 and §3 depend on them.

| Claim inherited | Finding | Evidence |
|---|---|---|
| "No Stage-1 build has occurred" | **A host-side Stage-1 build DID run (2026-10-13).** All five pilot workspaces exist and `C:\f2_pilot\build_manifest.json` records all five as `status="built"`. What has **not** occurred is a build under the bounded authorization proposed in `…S8_STAGE1_BUILD_AUTHORIZATION_REQUEST.md`. | `C:\f2_pilot\…` created 2026-10-13 17:12–17:17; `C:\f2_pilot\build_manifest.json` created 2026-10-13 17:15, 4,884 B, sha256 `4a91548943b0dad005407c35f2b627d4c0f691326d91daf538b60692c185ebf4`, five `built` entries |
| "No protected run has occurred" | **Correct, and stronger than stated: the S8-001 gate has NEVER consumed a grant on this host.** | `qwen_train/f2_authz_ledger.py:71` `DEFAULT_LEDGER_ROOT = <checkout>/data/f2_authz_ledger`, deliberately **not** environment-selectable; that directory is **absent**. Caveat: a run from a *different* checkout would have its own ledger. |
| "Credited admissions 4/300" | **The 4 credited admissions were produced by runs that predate the S8-001 gate.** | Gate added to the runner at `bee8d86a` (2026-10-15 23:01) and consumption at `2d5c0640` (2026-10-16 00:14); the ledger-writing runs are `pilot_run1/2/3.log` + `pilot_smoke.log` + `pilot_run_pip3.log`, mtimes 2026-10-14 22:54 – 2026-10-15 01:48, plus the django-guardian workspaces (2026-10-15 02:38/02:50). `pilot_smoke.log`'s traceback shows an unguarded `print("ledger sha256: …")`, i.e. code older than HEAD. |

Additional verified fact: **every one of the 9 rows in
`s8_pilot_ledger.jsonl` carries an EMPTY `repository_revision`** (read field-wise:
`rev=` for all 9). The runner writes `repository_revision` from the *verified
grant record*; an empty value is consistent with "no grant verified", and is
exactly the provenance the S8-001 binding exists to add.

These corrections do not change the mission; they change what "the first
execution" means. **The run being prepared would be the first S8-001-gated
execution in this repository's history.**

---

## 1. The execution-revision question

### 1.1 Which of the seven modified tracked files are on the execution path

Verified by reading the code, not the diff summaries.

| # | File | Change (verified) | Stage it touches | Needed for the run? |
|---|---|---|---|---|
| 1 | `qwen_train/f2_s8_pilot_live.py` | `main()` reordered: parse manifest → `build_specs` → unknown-`--only` refusal → zero-runnable refusal → `load_governance_from_env()` **before** `verify_and_consume`; "No grant was consumed" wording. Consumption itself unchanged. | **Protected execution behavior** + **authorization consumption order** | **Material, not strictly necessary** |
| 2 | `qwen_train/f2_s8_compose.py` | `_refs_ok` passes the real artifact-role key and `allowed_roles=tuple(REQUIRED_EVIDENCE_ROLES)` instead of a hard-coded single role. | **Evidence verification on the admission path** (`verify_evidence`) | **Material, not strictly necessary** |
| 3 | `qwen_train/f2_evidence.py` | **docstring only** (all 13 changed lines are inside `verify_task_evidence.__doc__`). | none | **No** |
| 4 | `qwen_train/f2_preflight.py` | real verification for `evidence.s8_base_gold` and `population.admission`; new `--admission-ledger/--registered-tasks/--s8-evidence`. | preflight / readiness reporting (Gate G tooling) | **No** |
| 5 | `qwen_train/f2_reconcile.py` | `_admission_accounting` separates the credited ledger union from the registration census. | reporting/accounting only | **No** |
| 6 | `tests/test_f2_s8_pilot_live_authorization.py` | tests for #1. | tests only | **No** |
| 7 | `docs/EXPERIMENT_J_F2_S8_IPV6_GATE_DECISION_RECORD.md` | +193 lines: 2026-10-16 superseding status update, §4b corrections, §8. | governance documentation | **No** (protected; sha256 `1a8501d6f0ba2036f8c8cf4045a8a80ca21b54c8b684d47e6e49426a972b1748`, must not change) |

Untracked, also on this path: `tests/test_f2_s8_runner_validation_order.py`
(pins #1; **structurally fails against `c2bfa856`** because it asserts
`build_specs(`/`load_governance_from_env()`/"zero runnable tasks" each appear
*before* `verify_and_consume(`), `tests/test_f2_preflight_evidence_sources.py`
(pins #4/#5), `tests/test_f2_sota_decisions.py` (pins #2/#3 and the dirty-tree
predicate).

### 1.2 Why #1 and #2 are "material but not strictly necessary"

- **#1.** With a valid manifest, a valid `--only`, and a fully provisioned
  environment, the committed order and the working-tree order behave
  **identically**. They differ only on failure: in the committed revision a
  manifest/`--only`/zero-runnable/readiness failure **consumes the single-use
  S8-001 grant and then refuses** (`verify_and_consume` precedes every check).
  A burned grant is one grant, one execution (`f2_authz_ledger.consumption_key`
  keys on the record digest), so the failure costs a **new owner signature**.
- **#2.** `_result_from_bundles` (`f2_s8_compose.py:609-623`) keys `artifacts`
  exactly by the factory's `REQUIRED_EVIDENCE_ROLES`. `TrustedArtifactStore.verify_ref`
  (`f2_governance.py:438-470`) performs **no stored-role comparison** — its role
  test is only `ref.role in allowed_roles`. The committed code passes
  `role="test_output"`, `allowed_roles=("test_output",)`, so the role test is
  **vacuously true for every ref**. A correctly built result is therefore
  admitted under **both** revisions; the working tree makes the check real
  (closed allowlist, fails closed on a key outside it).

Files #3–#7 are documentation, reporting, or tests and cannot affect the run's
admission. **None of the seven is required for a successful run.**

### 1.3 The "authorized patch set" route does not exist

A patch applied into the execution checkout produces a non-empty
`git status --porcelain`. `f2_authorization._check_binding` denies exactly that
whenever `repository_revision` is required — and `S8-001`/`base_gold_production`
requires it (`f2_authorization.py:124-126`):

```python
if required and repo_ident["dirty"]:
    raise AuthorizationError("working tree is dirty; a protected operation may "
                             "only run from a checkout whose code equals the bound "
                             "commit … (fail-closed)")           # :599-604
```

A dirty tree also fails the binding before that, because the executing code can
no longer be proven to equal the bound commit. So the hardening can only be
delivered **in a commit** — i.e. as a *revision*. The choice is strictly between
the committed revision and a newly committed one.

### 1.4 The three candidate strategies, compared

| | **R1 — execute `c2bfa856` as-is** | **R2 — land the hardening, execute the new revision** | **R3 — patch set in the worktree** |
|---|---|---|---|
| Publication needed | none | yes (commit on the branch; PR #33 stays OPEN) | none |
| Clean-tree predicate | satisfied (detached worktree at HEAD) | satisfied (detached worktree at the new SHA) | **DENIED** (`:599-604`) |
| Single-use grant on a readiness failure | **burned** → new signature | survives → retryable | not reachable |
| Artifact-role check on admission | vacuous | non-vacuous | n/a |
| Preflight evidence gates | literal placeholders | real (`F2-IMPL-AUTH-033`-consistent) | n/a |
| Admission accounting | census/admission conflated | separated | n/a |
| Verdict | workable; **not** the chosen revision | **RECOMMENDED** | **not viable** |

### 1.5 Recommendation

**R2**, on evidence, not convenience:

1. The first-ever S8-001-gated run has an elevated readiness-failure probability
   (new gate, new trust root, new env provisioning, guest channel, first
   `--only` use). Under R1 the first such failure spends the grant.
2. #2 removes a check that is **vacuous in the committed revision** on the very
   evidence path the run exists to exercise — a scientific-verification defect,
   not a style issue.
3. R1 additionally leaves Gate G's evidence gates as literal findings
   (`f2_preflight.py` committed revision), so the preflight cannot actually
   verify supplied S8 evidence before the run.

**What R2 does not do:** it does not make the run authorized; it does not create
a trust root or a grant; it does not prove isolation; it does not discharge any
of Gates A–G.

### 1.6 The one owner decision

> **Which revision may the S8-001 grant bind, given that the hardening cannot be
> delivered as a working-tree patch?**
>
> **(a) Recommended — authorize landing the S8 hardening as revision(s) on
> `agent/q9-classification-sota` (one logical change per commit, `FIX(s8):` /
> `FIX(f2):`; PR #33 remains OPEN and unmerged), then bind the grant to the
> resulting SHA.** Technical consequences: no grant is consumed on a readiness
> failure; the artifact-role check becomes non-vacuous; the preflight evidence
> gates become real; the admission census/admission distinction is enforced.
> Costs: one publication decision, plus a fresh clean-checkout verification at
> the new SHA (the 414-passed evidence is a working-tree property and must be
> re-established for the committed revision).
>
> **(b) Execute `c2bfa856` as-is.** Technical consequences: no publication; the
> committed code is executed unchanged; a readiness/manifest/`--only`/env failure
> **consumes the S8-001 grant** and requires a new owner signature; the
> artifact-role check stays vacuously true; the preflight evidence gates stay
> literal. This option is legitimate only with an out-of-runner readiness
> pre-check (§2.5) and an expectation of re-signing.

Note for the owner: `F2-IMPL-AUTH-033`'s **file boundary** does not name
`qwen_train/f2_s8_pilot_live.py` — nor `f2_s8_compose.py`, `f2_reconcile.py`,
`f2_preflight.py` or `f2_evidence.py` — and no other record names them either
(verified against every file boundary in the implementation-authorization
document). Option (a) therefore requires an operator record naming the S8 runner
hardening. That record has now been drafted in full — files, behaviour, tests,
rationale and explicit non-requests:
`docs/EXPERIMENT_J_F2_S8_RUNNER_HARDENING_AUTHORIZATION_REQUEST.md`. The decision
remains the owner's; it is stated here rather than decided.

---

## 2. The isolated execution checkout

### 2.1 Target

- **Path:** `C:\Users\rober\f2_exec\<REV>` — outside the repository, so the
  repository's own dirty tree is irrelevant to the run.
- **Revision `<REV>`:** the revision chosen in §1.6.
- **Existing worktrees (do not touch):** the main checkout at `c2bfa856`
  (`agent/q9-classification-sota`) and an unrelated
  `C:/Users/rober/Projects/swe_probe_work/sandbox_bugs/repo` (detached
  `45d9f619`). No execution worktree exists today.

### 2.2 Commands (prepared, NOT executed — no authorization covers creation yet)

```powershell
# 1. create (detached, so no branch is moved and nothing is published)
cd C:\Users\rober\Projects\v-horseshoe-v2
git worktree add --detach C:\Users\rober\f2_exec\<REV> <REV>

# 2. verify the revision the grant will bind
git -C C:\Users\rober\f2_exec\<REV> rev-parse HEAD          # MUST equal <REV>
git -C C:\Users\rober\f2_exec\<REV> status --porcelain      # MUST be empty

# 3. verify the code the runner will actually import comes from THIS checkout
cd C:\Users\rober\f2_exec\<REV>
C:\Users\rober\Projects\v-horseshoe-v2\.venv\Scripts\python.exe -c "import qwen_train, sys; print(qwen_train.__file__)"
# MUST print C:\Users\rober\f2_exec\<REV>\qwen_train\__init__.py
```

### 2.3 Why these are the correct commands (each item verified)

| Property | Evidence |
|---|---|
| The revision reported for a linked worktree is that worktree's HEAD | `repository_identity` uses `git -C <repo> rev-parse HEAD` with `repo = Path(__file__).resolve().parents[1]` (`f2_authorization.py:168-220`); pinned by `tests/test_f2_authz_binding_and_replay.py::test_repository_identity_worktree_shares_head` |
| The clean-tree predicate evaluates **against that checkout** | same function; `dirty := bool(git status --porcelain)`, enforced at `f2_authorization.py:599-604` |
| `git status --porcelain` includes untracked, non-ignored files (broad predicate) | `docs/EXPERIMENT_J_F2_S8_DIRTY_TREE_PREDICATE_DECISION.md` §2 — `PROVEN IN CURRENT REVISION` |
| The run must be invoked as `-m` **with CWD = the worktree** | `python -m` puts the CWD on `sys.path[0]`, so `qwen_train` resolves to the worktree. `.venv\Lib\site-packages` contains editable installs for **`organism_console` and `twine` only** — **no project-root `.pth`** — so nothing shadows the worktree copy. Invoking the script by its *main-repo* absolute path instead makes `Path(__file__).resolve().parents[1]` the **main repo** → `dirty=True` → denied. |
| Trust root and grant must live **outside** the worktree | The default `qwen_train/authorization/trust_root.json` is **not** gitignored (measured: `git check-ignore -v` → rc=1, no match), so placing it in the checkout creates an untracked file → `dirty` → denied. Use absolute `F2_AUTHORIZATION_TRUST_ROOT` and `F2_AUTHORIZATION_RECORD`. |
| The authorization ledger will land **inside** the checkout | `DEFAULT_LEDGER_ROOT = <checkout>/data/f2_authz_ledger` (`f2_authz_ledger.py:62-84`), **deliberately not environment-selectable**. `data/` is gitignored (`**/data/`, `.gitignore:44`), so it does **not** dirty the tree. **Consequence: the consumption record dies with the worktree. Retain the checkout as evidence, or the same grant becomes replayable.** |
| No `.env` in the worktree | `.env` and `**/.env` are gitignored (`.gitignore:4-5`), so a linked worktree has none. The S8 runner does **not** load dotenv (`load_governance_from_env` reads `os.environ` only); `f2_preflight` loads `<checkout>/.env` via an **allow-listed** `_load_operator_env(root)`. So from the worktree, `SWARM_RECEIPT_KEY` is absent unless **exported into the process environment**; writing `<WT>\.env` is also safe (gitignored, does not dirty). Values are never printed. |
| Build outputs and the manifest are outside the worktree | `f2_s8_pilot_build.ROOT = Path(r"C:\f2_pilot")` (`:53`), `INPUTS`/`PATCHES` under `C:\Users\rober\F2_EVIDENCE\s8\…` (`:50-51`) — all absolute constants, never CWD-derived |
| Evidence locations stay consistent | `--ledger` → the existing `<…>\trusted_store\s8_pilot_ledger.jsonl` (continues the registered chain; `--staging`/`--out` → `C:\Users\rober\F2_EVIDENCE\s8\{staging,pilot_results}` — all outside the worktree, per `…PACKAGE.md` §13 |

### 2.4 Required environment for the run (names only; values are the owner's)

Process-scoped, never committed, never printed:

```
F2_AUTHORIZATION_TRUST_ROOT        = <absolute path outside the worktree>
F2_AUTHORIZATION_TRUST_ROOT_SHA256 = <lowercase sha256 of that file's bytes>
F2_AUTHORIZATION_RECORD            = <absolute path outside the worktree>
SWARM_RECEIPT_KEY                  = <session scope only>
```

plus the governed inputs of `…PACKAGE.md` §13 (`SWARM_F2_*` with `SWARM_S8_*`
fallbacks per `f2_s8_compose.S8_ENV_FALLBACKS`, and the three S8-only names
`SWARM_S8_ENVIRONMENT_IDENTITY` / `SWARM_S8_BASE_STATE_DIGEST` /
`SWARM_S8_GOLD_STATE_DIGEST`, which have no fallback).

### 2.5 Failure-mode note if the owner chooses R1

Under R1 the readiness checks must run **outside** the runner, because the
runner's own refusal no longer protects the grant. Minimum pre-check before
invoking it: manifest parses as JSON; every entry the runner will select has
`status == "built"`; `--only` (if used) names only ids present in the manifest;
`load_governance_from_env()` succeeds. This narrows but does not close the window
(the runner re-reads the manifest itself), and it does not fix the vacuous
role check.

---

## 3. The bounded Stage-1 build

### 3.1 Invocation (as prepared by
`docs/EXPERIMENT_J_F2_S8_STAGE1_BUILD_AUTHORIZATION_REQUEST.md` §2; confirmed
against the code constants)

```powershell
# CWD = the execution checkout (so the authorized revision's builder is used)
.\.venv\Scripts\python.exe -m qwen_train.f2_s8_pilot_build `
  --manifest "C:\f2_pilot\build_manifest.json" `
  --inputs   "C:\Users\rober\F2_EVIDENCE\s8\pilot_inputs\full" `
  --patches  "C:\Users\rober\F2_EVIDENCE\s8\pilot_build\patches" `
  HypothesisWorks__hypothesis-4532 `
  openai__openai-agents-python-1633 `
  openai__openai-agents-python-1636 `
  python__mypy-19767 `
  stanfordnlp__dspy-8739
```

`--manifest`, `--inputs`, `--patches` are the code defaults
(`f2_s8_pilot_build.py:347-351`), so the bare form
`… -m qwen_train.f2_s8_pilot_build <five ids>` is equivalent. The builder writes
to `C:\f2_pilot` regardless of CWD (absolute constant).

### 3.2 Confirmed inputs

- **Five task ids**, exactly those above; `REPOS` maps them to **four distinct
  URLs** (`openai__openai-agents-python` is shared by `-1633` and `-1636`):
  `HypothesisWorks/hypothesis`, `python/mypy`, `stanfordnlp/dspy`,
  `openai/openai-agents-python` (`f2_s8_pilot_build.py:65-71`).
- **Input set** (`pilot_inputs/full` 5 files + `pilot_build/patches` 10 files)
  present; the SHA-256 pin and aggregate digest are in the request doc §3.
- **Permitted destinations / dependency sources:** the four pinned GitHub URLs
  and the Python index for the declared distributions only; `pytest==8.3.5` plus
  `EXTRA_DEPS` (`python__mypy-19767` → `pytest-xdist`; `stanfordnlp__dspy-8739`
  → `datamodel-code-generator`, `pillow`) and each project's own declared pytest
  plugins, as computed by `builder_install_plan`.

### 3.3 Pinned paths

| Purpose | Path |
|---|---|
| Workspace root (fixed, absolute) | `C:\f2_pilot\` |
| Per task | `C:\f2_pilot\<task_id>\{repo,repo_gold,\.venv,\.venv_gold}` (uv-owned tasks: `repo\.venv`, `repo_gold\.venv`) |
| Build manifest (the `--manifest` default) | `C:\f2_pilot\build_manifest.json` |
| Task records | `C:\Users\rober\F2_EVIDENCE\s8\pilot_inputs\full\<task_id>.json` |
| Patches | `C:\Users\rober\F2_EVIDENCE\s8\pilot_build\patches\<task_id>.{test,gold}_patch` |

### 3.4 ⚠ Pre-existing state — read before authorizing the build

`C:\f2_pilot` is **not empty**: five pilot workspaces (2026-10-13) + two
`django-guardian__django-guardian-{896,899}` workspaces (2026-10-15) +
`build_manifest.json` (4,884 B, sha256 `4a915489…`, five `status="built"`).

Two behaviours make this consequential:

1. **The build destroys and recreates existing workspaces.** `build()` begins
   with `_rmtree(repo)`, `_rmtree(gold_repo)` (`:239-240`) and `_rmtree(venv)`
   (`:283`). A re-run therefore **deletes the 2026-10-13 trees** that produced
   the retained `pilot_build\dryrun\` artifacts. The request doc names
   `_rmtree` but does **not** record this consequence; it should.
2. **The manifest is MERGED, not reset.** `main()` reads an existing manifest and
   overwrites only the entries it rebuilds (`:359-379`). A subset run leaves
   stale `status="built"` entries, and `build_specs` selects **every**
   `status="built"` entry — so a stale entry would put an **unauthorized
   workspace** into the run's runnable set.

**Therefore:** either rebuild all five, or start from a manifest containing
exactly the authorized task set, and compute the manifest digest that the grant
will bind **after** the build and before the grant is signed.

### 3.5 How failure and partial state are represented (verified)

- **Failed build:** `manifest[task_id] = {"status": "build_failure", "detail": <first 2000 chars of the exception>}`; the batch continues; `main()` still returns **0**. Only the manifest **status**, never the exit code, distinguishes a built task (`:370-382`).
- **Partial workspace:** left in place on failure (`build()` does not clean up on exception); the *next* run removes and recreates it via the leading `_rmtree`s. Destruction is bounded to `C:\f2_pilot\<task_id>`.
- **Manifest written incrementally:** rewritten after each task (`:377-379`), so a crash leaves a partial-but-valid manifest.
- **The builder reads no credential** and no `.env`; a task needing a secret is a `build_failure`.

### 3.6 What must be independently verified before a grant is signed

1. `sha256(C:\f2_pilot\build_manifest.json)` — this is `candidate_manifest_digest`.
   Record it; the runner reads the file **once** (`read_bytes`, no TOCTOU window),
   so any later edit invalidates the grant.
2. **Entry set:** exactly the authorized five ids (no stale sixth).
3. **Per task:** `status == "built"`; `base_commit` equals the `base_commit` in
   `pilot_inputs/full\<task_id>.json`; `workspace == C:\f2_pilot\<task_id>`;
   `repo`/`gold_repo` exist and `git -C <repo> rev-parse HEAD` equals
   `base_commit` with the pinned test/gold patches applied; for non-uv tasks
   `python`/`gold_python` are non-null and `.venv`/`.venv_gold` exist;
   `uses_uv` correct; `install_plan["base"]`/`["gold"]` present.
4. **`registration_sha256`** — read from the operative registration, never
   typed: `qwen_train/curriculum/f2_registration/registration_manifest.json`
   (`"operative": true`) → `registration_content_sha256 =
   5f3c9f47eb622d7d983cfdcb6ebd3d240f6936087a049781c741ba37e237f042`, with
   `registration_id = evaluator_compat_python_confirmed_v1`.
5. **`repository_revision`** — `git rev-parse HEAD` of the execution checkout
   (§2.2). The verifier derives it independently and compares; it is never
   trusted from the caller (`f2_authorization._check_binding:582-598`).

The grant must bind all three of `repository_revision`,
`candidate_manifest_digest`, `registration_sha256` (`f2_authorization.py:124-126`);
a missing one is refused.

### 3.7 Not to be done before the authorization exists

No clone, no `pip`/`uv` install, no build, no VM/guest action, no manifest write.

### 3.8 Network requirements differ by stage (build ≠ protected execution)

The two stages have **opposite** network requirements and must not be conflated.

| Stage | Where | Network needed | Source |
|---|---|---|---|
| **Stage-1 build** (`f2_s8_pilot_build.py`) | host, `C:\f2_pilot` | **Public egress**: `git clone --filter=blob:none` of the four pinned GitHub URLs, plus the Python index for the declared distributions (`builder_install_plan`) | `f2_s8_pilot_build.py:244` (clone), `:53` (`ROOT`); `F2_S8_EXECUTION_AND_PILOT_PACKAGE.md` §1 — build boundary: "Internet: only if separately authorized" |
| **Protected execution** (`f2_s8_pilot_live.py`) | guest, `C:\f2_s8` | **No internet.** Offline only: `per_task_env` sets `UV_OFFLINE=1` and `UV_FIND_LINKS=WHEELHOUSE`; route to `10.72.0.0/24` prohibited; "offline dependency availability" is an explicit pilot measurement | `f2_s8_pilot_live.py:65,192,211,216`; `F2_S8_EXECUTION_AND_PILOT_PACKAGE.md` §1/§4/§5.1 |

Consequences to hold in view:

1. The build **cannot** run with the protected-execution egress policy in force, and
   protected execution **must not** run with the build's egress open. Gate A's scope
   and Gate F's enforcement describe **different** environments.
2. The host↔guest channel is **PowerShell Direct** (`PsDirectChannel`), a
   privileged-host prerequisite (the host credential file), not a network route.
3. Gate F attests the **guest's** posture; nothing about the host repository's own
   connectivity is part of the attestation.

---

## 4. Owner-controlled prerequisites (instructions only)

Nothing in this section was performed. Owner procedure and exact formats are in
`docs/EXPERIMENT_J_F2_S8_OWNER_PREREQUISITE_PACKAGE.md` §4 and Gates B–F; the
points below are the ones that interact with the checkout design in §2.

1. **Trust root (Gate B).** Owner generates an Ed25519 keypair with their own
   tooling (private key never in the repository); publishes the **base64 of the
   32-byte raw** public key under a `key_id` in
   `{"signers": {...}, "revoked_key_ids": [], "revoked_record_ids": []}`; stores
   the file **outside the checkout**; sets `F2_AUTHORIZATION_TRUST_ROOT` to the
   absolute path. **Independent fingerprint verification:** compare
   `describe_trust_root()["digest_sha256"]` against a value obtained over a
   channel independent of this process, then export it as
   `F2_AUTHORIZATION_TRUST_ROOT_SHA256`. The pin is process-controlled
   consistency, **not** custody (`f2_authorization.py:361-379`).
   ```powershell
   .venv\Scripts\python.exe -c "from qwen_train.f2_authorization import describe_trust_root as d; import json; print(json.dumps(d(), indent=2))"
   ```
   This prints public material only (path, file digest, key ids, epochs,
   revocation lists) — no private key exists in a trust root.
2. **S8-001 grant (Gate C).** Only **after** the build manifest exists and its
   digest is recorded (§3.6). One signed record
   `{"record": {...}, "signature": {...}}`: `schema="f2-authorization/v1"`,
   `stage="S8-001"`, `operations=["base_gold_production"]`, `approver`,
   tz-aware `not_before`/`not_after`, `protocol_digest`, and the three required
   bindings; `alg="ed25519"`, `key_id` present in the trust root, `signer ==
   approver`, signature over `canonical_bytes(record)`. Point
   `F2_AUTHORIZATION_RECORD` at it (outside the checkout). It is **single-use**:
   the markdown plan warns that a *superseded* draft must be re-signed if any
   binding changes.
3. **Governed environment (Gate D).** Export the names in §2.4 for the run
   scope only, in the process environment of the launching shell. **Never commit,
   never print values.** Verify by presence only, e.g.
   `-c "import os;print(sorted(k for k in os.environ if k.startswith('SWARM_') or k.startswith('F2_AUTH')))"`.
   Confirmed on this host: `SWARM_RECEIPT_KEY` is present in the repository
   `.env` (allow-listed loader) but **ABSENT** from the raw process environment,
   and the 13 evaluator/S8 names are absent.
4. **Privileged egress enforcement and attestation (Gate F).** Enforcement is a
   privileged host action (Hyper-V extended ACL on `F2-S8-VM1`); the attestation
   is produced in-guest. Verifier entry point:
   ```powershell
   .venv\Scripts\python.exe -m qwen_train.f2_isolation `
     --out <attestation.json> `
     --enforcement-digest <sha256 of the HOST-captured enforcement artifact> `
     --external <host:port> [--external …] [--required-service NAME=HOST:PORT]
   ```
   `verify_isolation_attestation` is fail-closed and needs the **host-supplied
   capture as a separate argument**; a timeout or unreachable route is not a
   verified denial. The already-existing 2026-10-14/15 pairs verify; reusing
   them is an owner call, and "the enforcement evidence verifies" is not "the
   policy was re-applied".
5. **Exact-operation preflight (Gate G).** Run it **from the execution
   checkout** so `repository_identity` matches the bound revision:
   ```powershell
   cd C:\Users\rober\f2_exec\<REV>
   C:\Users\rober\Projects\v-horseshoe-v2\.venv\Scripts\python.exe -m qwen_train.f2_preflight --json `
     --attestation "<…>\isolation_run_2026-10-15\attestation_reval_bound.json" `
     --enforcement-capture "<…>\isolation_run_2026-10-15\enforcement_capture_revalidate.json" `
     --conversion-record "<…>\conversion\conversion_record.json" `
     --admission-ledger "<…>\trusted_store\s8_pilot_ledger.jsonl" `
     --admission-ledger "<…>\trusted_store\s8_pilot_ledger_corrected.jsonl" `
     --registered-tasks "<a JSON list of the registered task ids>"
   ```
   Require exit 0 and no blocking findings. Remember: from the checkout,
   `SWARM_RECEIPT_KEY` is absent unless exported, so `env.receipt_key` becomes an
   extra blocker if it is only in the main repo's `.env`.

---

## 5. Conditions under which execution may begin

All of the following, in order, and none of them satisfied today:

1. §1.6 decided, and — if (a) — the operator record requested in
   `docs/EXPERIMENT_J_F2_S8_RUNNER_HARDENING_AUTHORIZATION_REQUEST.md` granted, the
   hardening published, the resulting SHA recorded, and the focused-suite evidence
   re-established **for that commit**.
2. Gate A: a numbered authorization record naming the builder, the command, the
   five ids, the permitted scope, the limits, the stop conditions and the
   prohibited operations (`…STAGE1_BUILD_AUTHORIZATION_REQUEST.md` §6).
3. The authorized Stage-1 build executed; manifest digest recorded; §3.6 checks
   green.
4. Gate B: trust root provisioned **outside** the checkout and pinned
   out-of-band.
5. Gate C: one signed S8-001 grant bound to `repository_revision` +
   `candidate_manifest_digest` + `registration_sha256`.
6. Gate D: governed env exported for the run scope; values never printed.
7. Gate F: egress enforced and the attestation **with its capture** verified.
8. The isolated checkout created and verified (§2.2), retained for the life of
   the consumption record.
9. Gate G: `f2_preflight` exit 0 for the exact scope, run from that checkout.
10. The `--gates` JSON still contains all eight of `REQUIRED_GATES`
    (`f2_s8_pilot_live.py:50-54`) with every value `true` — these are
    **preconditions, not authority**.

While any condition is unmet: no clone, no install, no build, no VM/guest
action, no model/T/X arm, no Q10/Q12/Q13.

---

## 6. Observations that are not blockers (recorded, not acted on)

- `F2-S8-VM1` is **Running** (uptime 07:45:46, read-only `Get-VM`), which
  contradicts `…PACKAGE.md:422` ("confirm `F2-S8-VM1` is cleanly powered off
  before session initiation"). Recorded as a live-state deviation; no action
  taken.
- `C:\f2_tooling`, `C:\posix`, `C:\f2_s8` do **not** exist on the host. Per the
  code they are **guest-side** paths (`TOOLING_ROOT`, `POSIX_ROOT`, `UV_ROOT`,
  `GUEST_DIR`), and `stage_spec_for_guest` only puts `spec.json` into the guest,
  so their host absence is consistent with host/guest separation. Whether the
  guest currently holds them is `NOT ESTABLISHED` from the host and would need an
  authorized guest session.
- `C:\Users\rober\F2_EVIDENCE\s8_cred.xml`, `C:\Users\rober\F2_ARTIFACTS`,
  `…\s8\pilot_deliver`, `…\s8\staging`, `…\s8\pilot_results` exist;
  `pilot_gates.json` holds all eight gates `true`.
- **`F2-IMPL-AUTH-033`'s implementation commits touched files outside its declared
  file boundary** — `bee8d86a`, `2d5c0640` and `fc88c406` all modify
  `qwen_train/f2_s8_pilot_live.py`, and `fc88c406` also `qwen_train/f2_s8_compose.py`,
  neither of which the boundary names (`PROVEN`, `git show --stat`). Recorded, not
  acted on: it is why a fresh operator record naming these files is required before
  further changes land on them (§1.6 note; the authorization-request document).

---

## 7. Explicit non-claims

- This record grants no authorization, marks nothing approved, and creates
  nothing outside itself.
- No worktree, build, clone, install, network, VM, guest, model, Q10/Q12/Q13 or
  protected run was performed for it.
- Test evidence for the working tree was **re-established** on 2026-10-16: the
  focused F2 suite `tests/ -k f2` (which includes the three new test files) reported
  **2763 passed, 4 skipped, 0 failed**; the four directly-affected test files reported
  **40 passed**; `git diff --check` was clean; and before/after SHA-256 snapshots of
  the production stores were byte-identical (zero mutations observed). Its scope is
  the **working tree**, not `c2bfa856` — so option (a) still requires the evidence to
  be re-established for the committed revision.
- It changes no authority document, no protected document
  (`EXPERIMENT_J_F2_S8_IPV6_GATE_DECISION_RECORD.md` sha256
  `1a8501d6…` unchanged), and no code.
- `HISTORICALLY CLAIMED` from `…OWNER_PREREQUISITE_PACKAGE.md` is reused only
  where re-verified above; §0 corrects the three items that did not survive.
- Passing any synthetic test does not prove a real build, a real signing
  ceremony, real isolation, or that F2 is ready.
