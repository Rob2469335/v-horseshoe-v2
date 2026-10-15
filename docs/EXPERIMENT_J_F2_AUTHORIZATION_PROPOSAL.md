# Experiment J / F2 — Authorisation Proposal (POP-001 / EXPL-001 / CONF-001)

**Status: PROPOSED — all three authorisations are `PENDING`. No approval is implied.
This document does not authorise execution of any stage.**
Append-only decision record; does not amend F0 (`docs/EXPERIMENT_J.md`, frozen
`20a1989b`), F1, or any frozen endpoint/T-X-C0/k/α/CI/McNemar semantics.

Approver: the owner. Mechanism: owner-written approval recorded in this file's
decision log below (authority per `AGENTS.md` §1 and the F2 authorisation log);
a code check that merely tests a filename or self-declared status is **not** a
sufficient approval record (see Enforcement, below).

---

## D1 — SAP proposal vs code-enforced floor (RESOLVED in documentation only)

`docs/EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md` §2 is **PROPOSED and
NONBINDING** as scientific design. The repository implements a **code-enforced
minimum sample-size floor** `n = max(300, required_pairs(...))` (§2.4), pinned by
`tests/test_f2_statistics.py::TestRequiredPairsInterpretation` and
`...::test_every_authorized_sensitivity_point_stays_below_the_frozen_minimum`.
That floor is an implementation invariant, **not** a scientific authorisation.
Scientific authorisation of the SAP (test, α, power, δ, π_d, n) is
**REQUIRES AUTHORIZATION** (§6). Evidence: SAP §2 header clarification (2026-10-11),
SAP §2.4.

Classification: **GOVERNANCE GAP** (proposal vs "frozen" wording) — now documented
as such; **NOT** a frozen protocol.

---

## D2 — δ / π_d (design cannot currently be powered)

`δ` (minimum relevant paired risk difference) and `π_d = P(T xor X)` are both
**empirical** (SAP §2.5) and **NOT ESTABLISHED**:
`docs/EXPERIMENT_J_F2_EXPLORATORY_AUTHORIZATION.md:14,25-26,36` — *"a confirmatory N
therefore cannot be computed"*; the no-lesson calibration has no T arm (§3, Q10
`CALIBRATION_ARMS=("X","C0")`). `required_pairs()` refuses `δ > π_d`.

Options (owner decision required; do **not** pick the one that makes 300 easiest):

| Option | Assumption | Risk / consequence | Approval |
|---|---|---|---|
| **A. Estimate from a separate exploratory screening population** | exploratory T/X measures `π_d` (and `δ`); screening data are NOT reused for confirmatory inference | needs its own population + evaluator identities; estimate uncertainty must be propagated (a point estimate understates n); carries selection risk if not disjoint from confirmatory pool | **EXPL-001**, then **CONF-001** |
| **B. Prespecified δ/π_d from independent evidence** | owner fixes `δ`,`π_d` from external/prior evidence, preregistered before any F2 outcome | must be justified and frozen pre-outcome; wrong values invalidate power | owner decision + **CONF-001** |
| **C. Any method already in the protocol** | none found beyond the exploratory authorisation | — | — |

**Not established:** that reusing screening observations for confirmatory
inference is valid (selection/over-fitting) — treat as **NOT ESTABLISHED**; the
exploratory run is screening-only (`EXPLORATORY_AUTHORIZATION.md:90,103-104`).

---

## POP-001 — Population acquisition  (*PENDING*)

Permitted: acquire the pinned SWE-rebench-V2 revision (verify the exact sha; a
historical claim is `10483de0f50fe5da545942705a76c6150171af7f` — **NOT ESTABLISHED**
until re-verified) and the SWE-bench-Live source for the F2 screen; pin revisions;
verify source hashes and licences; emit a read-only acquisition manifest with per-
record source ids.
Outputs: `<data>/f2_population/acquisition_manifest.json` (+ hashes).
Forbidden: admission, normalisation eligibility decisions, any execution, any
confirmatory claim.
Gate to next stage: normalisation runs only after POP-001 approved.

## EXPL-001 — Exploratory T/X contrast  (*PENDING*)

Objective: measure `π_d` and `δ` (paired risk difference) for T vs X on the approved
screening population, **exploratory only**.
Unit: paired task×seed; endpoint per F0 (`k`, frozen relevant_file_set).
Permitted: execute the paired T/X screen; persist provenance + receipts.
Forbidden: any confirmatory significance claim
(`EXPLORATORY_AUTHORIZATION.md:90,105`); promotion into the confirmatory record.
Stopping: by the exploratory authorisation (to be fixed at approval).
Gate: CONF-001 may be powered **only** after EXPL-001 (or B) resolves `δ`,`π_d`.

## CONF-001 — Confirmatory F2  (*PENDING*)

Design (per SAP §2, proposed): exact conditional McNemar on `b+c`, **two-sided**,
`α = 0.05`, target power `0.80`, `n = max(300, required_pairs(δ, π_d, 0.05, 0.80,
two-sided))`, 95% **Clopper-Pearson** CI; report `m,b,c,π̂_d,Δ̂`,exact p, CI.
Exclusions/missing-data/stopping: per F0 §16 + SAP (proposed).
Authorisation: owner signature below, after `δ`,`π_d` are fixed (D2).

---

## Decision log (owner)

| ID | Decision | Status | Date | Owner signature |
|---|---|---|---|---|
| D1 | SAP §2 is proposed/nonbinding; floor is code-enforced only | **RECORDED (doc)** | 2026-10-11 | |
| D2 | δ/π_d source = A / B / other | **PENDING** | | |
| POP-001 | Population acquisition | **PENDING** | | |
| EXPL-001 | Exploratory T/X | **PENDING** | | |
| CONF-001 | Confirmatory F2 | **PENDING** | | |

---

# Appendix G — Governance closure (2026-10-11)

Documentation-only. No runtime/authorization semantics changed. Labels: PROVEN /
SUPPORTED / INFERRED / NOT ESTABLISHED / GOVERNANCE GAP / REQUIRES OWNER INPUT /
REQUIRES AUTHORIZATION.

## G.1 Authorization-model decision — Q10 vs the signed verifier
Two mechanisms exist: (a) the signed-attestation verifier
(`qwen_train/f2_authorization.py`) for POP-001/EXPL-001/CONF-001; (b) the older
in-process `CalibrationAuthorization` gate for Q10 (`qwen_train/f2_calibration.py`,
authoritative `F2-IMPL-AUTH-010`; fails closed). They are **not equivalent**.
| Option | Security/ops | Authority compatibility | Migration/test | Risk |
|---|---|---|---|---|
| A. Keep Q10 on `CalibrationAuthorization` | Works today; fail-closed; no signature/trust root | Matches AUTH-010 | none | **Split authorization semantics** (two models) |
| B. Unify Q10 under the signed verifier | One model; cryptographically verifiable | Requires amending AUTH-010 + a defined Q10 stage | New stage/scope + tests | Changes Q10's existing authorization contract; needs owner sign-off |
**Recommendation: PROPOSED — OWNER DECISION REQUIRED.** Do not implement either
change or invent a Q10 stage here.

## G.2 Independent trust-root ceremony — REQUIRES OWNER INPUT
Minimum owner-controlled steps (facts the owner must supply are `REQUIRES OWNER
INPUT`): who may approve the trust root; out-of-band authentication of the public
trust root; custody of the signing key **outside** the repo and dev process; key
identity/rotation/revocation/expiry/recovery; verification of the provisioned root
without exposing private keys; behaviour on missing/invalid/compromised root (deny);
evidence to record before production authorization is available. **No key, root,
or signature is created here.**

## G.3 GitHub governance boundary — PROPOSED — OWNER/GITHUB ADMIN ACTION REQUIRED
Live read-only check (2026-10-11): `master` branch protection **absent**
(`gh api …/branches/master/protection` → 404 "Branch not protected"); rulesets
**none** (`…/rulesets` → `[]`); secret scanning + push protection enabled.
Proposed (not active): a `master` ruleset requiring PR + required checks
(`pytest`, `ruff`) + required review, no bypass/self-approval; CODEOWNERS on
`f2_authorization.py`, `f2_governance.py`, `f2_readiness.py`, `f2_calibration.py`,
`f2_population_acquire.py`, `f2_registration.py`, `f2_arm_orchestrator.py`,
`f2_execution_adapter.py`, and the CODEOWNERS/workflow files; emergency-change
procedure. Verify the files/workflows actually exist before activating.

## G.4 Count separation and the 300-pair path
Never interchange: (1) acquired records, (2) admitted eligible records,
(3) completed observations, (4) **analyzable paired task×seed units**. 300 is a
sample-size target, **not** statistical sufficiency or permission to execute.
Dependency chain (no step implies the next is authorized): 1 docs reconciled →
2 Q10 model owner decision → 3 independent trust boundary → 4 GitHub controls →
5 POP-001 authorization → 6 acquire/admit → 7 EXPL-001 authorization → 8 CONF-001
authorization → 9 execute approved workflow, counts reported separately → 10 reach
300 analyzable pairs only if the approved protocol still requires it.

## G.5 AUTH-013 / AUTH-024 / AUTH-028 reconciliation
- `F2-IMPL-AUTH-013` (`EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md:297`):
  statistical design authorization — "exact McNemar, pi_d=0.50, n=300",
  AUTHORIZED 2026-10-05; **explicitly does not authorize population acquisition**.
- `F2-IMPL-AUTH-024` (`docs/EXPERIMENT_J_F2_EXPLORATORY_AUTHORIZATION.md:3`):
  exploratory T/X pilot; the frozen confirmatory design **cannot currently be
  validly powered**; `pi_d`/`delta` **NOT ESTABLISHED**.
- `F2-IMPL-AUTH-028` (`…ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md:1597`):
  population acquisition and admission screening — RECORDED 2026-10-07.
**Conflicts / GOVERNANCE GAP (owner decision needed):** (i) AUTH-013 fixes
`pi_d=0.50` while AUTH-024 holds `pi_d` NOT ESTABLISHED — the planning assumption
and the measurement state are inconsistent; (ii) AUTH-028 records acquisition
authority while AUTH-013 states acquisition is not authorized by it — the operative
acquisition authority must be stated; (iii) whether the readiness tooling
recognizes AUTH-028 is a code question (`EXPERIMENT_J_F2_OPERATOR_HANDOFF.md:164`).
Resolve by naming the owning clause; do not pick the clause that enables execution.

---

# Appendix H — Authority-conflict decision record (2026-10-11)

Documentation only. No runtime/authorization/test changes.

## H.1 Conflict A — planning π_d=0.50 vs empirical π_d NOT ESTABLISHED
- **AUTH-013** (`…ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md:297`): "Statistical
  design authorization: F1 freeze, exact McNemar, **pi_d=0.50**, n=300" — records
  operator-granted *scientific parameters*; documentation only.
- **AUTH-024** (`docs/EXPERIMENT_J_F2_EXPLORATORY_AUTHORIZATION.md:26`): "*π_d is
  NOT ESTABLISHED.*" and (":36") "*A confirmatory N therefore cannot be computed.*"
- **Reconciliation:** these are **different quantities** only if AUTH-013's 0.50 is
  labelled a **planning assumption** used for the n-floor. AUTH-013 does not say so
  explicitly, so as written the clauses read as contradictory.
- **Verdict: GOVERNANCE GAP (labelling), not a true conflict.** Owner decision
  required: record AUTH-013's `π_d=0.50` as a *planning assumption* (sizing the
  `n = max(300, required_pairs(...))` floor), explicitly distinct from the
  *empirical* `π_d` that AUTH-024 requires and which remains NOT ESTABLISHED.
  Closure evidence: an amended AUTH-013 clause stating the planning/empirical
  distinction (or an owner clarification entry).

## H.2 Conflict B — AUTH-028 acquisition vs AUTH-013 non-grant
- **AUTH-013** (`EXPERIMENT_J_F2_EXPLORATORY_AUTHORIZATION.md:141`): population
  acquisition is "**REQUIRES AUTHORIZATION**" and AUTH-013 "does not grant it".
- **AUTH-028** (`…ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md:1597`): "Population
  acquisition and admission screening (operator-granted)", Author Rob, **2026-10-07**;
  target "**≥300 admitted task pairs**"; "**If fewer than 300 tasks survive … STOP**".
- **Reconciliation:** AUTH-028 is **later** (2026-10-07) and **owns** acquisition/
  admission; AUTH-013 (2026-10-05) simply never granted it. No true conflict —
  AUTH-028 supersedes on this scope.
- **Verdict: PROVEN (reconciled by recency + scope).** Residual: whether the
  readiness tooling *recognizes* AUTH-028 is a code question
  (`EXPERIMENT_J_F2_OPERATOR_HANDOFF.md:164`), **NOT ESTABLISHED** here.
- **Unit identity:** AUTH-028's note classifies "one seed per task" as the
  authorized reading ("task×seed" names identity; the execution path has no seed
  parameter; `PairedObservation.seed` defaults to 0). So **300 analyzable paired
  task×seed units == 300 admitted task pairs, one seed each**.

## H.3 π_d=0.50 trace (statistical integrity)
- `f2_statistics.py:393,398,408` — `discordance` is a **required empirical input**
  ("both must be supplied, because both are empirical"): production logic does
  **not** hardcode π_d=0.50.
- `f2_statistics.py:169,174,222,224` and `f2_analysis.py:670` — the `0.5` literals
  are the **McNemar null** (binomial p=0.5 under H0), **not** π_d.
- **Finding:** π_d=0.50 is a **planning assumption** (AUTH-013) used for sizing;
  it is **not** baked into production logic. 300 is a *sample-size target*, **not**
  statistical sufficiency: sufficiency depends on the empirical π_d/δ and the
  approved design. Keep **acquired / admitted / completed / analyzable** counts
  strictly separate.

## H.4 GitHub governance acceptance package — PROPOSED — NOT ACTIVE
Live (2026-10-11, read-only): `master` protection **absent** (404), rulesets `[]`,
secret scanning + push protection enabled.
| Control | Property | Setting/file | Active evidence | Negative test | Owner action |
|---|---|---|---|---|---|
| PR required | no direct pushes to master | ruleset `required_pull_request_reviews` | ruleset read shows rule | push to master rejected | yes |
| Required review | independent approval | ruleset min approvals ≥1 | PR blocked w/o review | self-approve blocked | yes |
| Required checks | tests gate merges | ruleset required status checks = **actual** check names (from `gh api …/commits/master/check-runs`) | merge blocked on failing check | red check blocks | yes |
| Bypass/self-approval | no silent bypass | ruleset bypass list empty; `dismiss_stale_reviews` | ruleset read | admin bypass attempt logged | yes |
| CODEOWNERS | review on sensitive paths | `.github/CODEOWNERS` on `f2_authorization.py`, `f2_governance.py`, `f2_readiness.py`, `f2_calibration.py`, `f2_population_acquire.py`, `f2_registration.py`, `f2_arm_orchestrator.py`, `f2_execution_adapter.py`, CODEOWNERS + workflows | CODEOWNERS present + rule requires code-owner review | edit to a covered file requests review | yes |
| Protect CODEOWNERS/workflows | no self-weakening | CODEOWNERS covers itself + `.github/workflows/**` | as above | change requires review | yes |
| Emergency recovery | no silent bypass | documented break-glass (temporary, logged, post-hoc reviewed) | procedure + audit log | break-glass recorded | yes |

## H.5 Production trust-root acceptance criteria — REQUIRES OWNER INPUT
Acceptance (see also Appendix G.2): (1) owner-approved signer/approver authority
recorded; (2) public trust root authenticated **out of band**; (3) production
signing key custody **outside** the repo and ordinary dev process; (4) rotation/
revocation/expiry/compromise/recovery defined; (5) fail-closed on missing/invalid
root; (6) evidence recorded before production authorization is available.
**REQUIRES OWNER INPUT:** approver identity, custody arrangement, out-of-band
authentication method, recovery procedure. No key/root/signature created.

## H.6 Reconciliation status
Corrected/recorded: AUTH-013 vs AUTH-024 (labelling gap, H.1); AUTH-013 vs
AUTH-028 (reconciled, H.2); π_d usage (H.3); GitHub state + proposal (H.4);
trust-root criteria (H.5). **No clause treated as permission to execute.**

---

## Appendix H — CORRECTION (2026-10-11): readiness recognition of AUTH-028

Live source `qwen_train/f2_readiness.py:35-45` shows the readiness tooling **does**
recognize the applicable authority: it cites **AUTH-013** for the `n=300`
**analyzable** minimum and implements the `protected_population` prerequisite
(AUTH-020) by comparing against the **admitted** count — "exactly the target
AUTH-028 (operator, 2026-10-07) set: 'at least 300'". It also carries a
`q10_q12_q13_authorization` readiness item (`f2_readiness.py:69,90`).
Therefore the earlier "readiness recognition of AUTH-028 = NOT ESTABLISHED
(code question)" is **superseded → PROVEN**: the code recognizes AUTH-028's
admitted-population target and AUTH-013's analyzable minimum, and keeps the two
counts distinct. No readiness defect found; no code change required.
(Historical `EXPERIMENT_J_F2_OPERATOR_HANDOFF.md:164` predates this and is stale.)

---

# Appendix I — Minimum owner-action package (dependency order)

Each item: action · authority · inputs · completion evidence · independent
verification · what it unlocks (and what remains unauthorized).

## A. Trust-root establishment (unlocks: production authorization availability)
- **Action:** record approver identity; authenticate the public trust root
  out-of-band; keep the signing key outside the repo/dev process; define rotation/
  expiry/revocation/compromise/recovery; provision `qwen_train/authorization/trust_root.json`
  (public keys only).
- **Authority:** owner. **Inputs:** `REQUIRES OWNER INPUT` (approver identity,
  custody, out-of-band method, recovery).
- **Completion evidence:** the trust-root file exists with the owner's public key;
  a signed record verifies against it; fail-closed confirmed for missing/invalid.
- **Verification:** `f2_authorization.verify_authorization` accepts a genuinely
  owner-signed record and rejects a self-signed one. **Does NOT authorize any
  scientific execution.**

## B. GitHub governance (unlocks: no silent self-merge of enforcement/trust paths)
- **Action:** enable a `master` ruleset — required PR + required independent
  review + required status checks (use **verified** check names from
  `gh api repos/Rob2469335/v-horseshoe-v2/commits/master/check-runs`), no bypass/
  self-approval; CODEOWNERS on `f2_authorization.py`, `f2_governance.py`,
  `f2_readiness.py`, `f2_calibration.py`, `f2_population_acquire.py`,
  `f2_registration.py`, `f2_arm_orchestrator.py`, `f2_execution_adapter.py`,
  `.github/CODEOWNERS`, `.github/workflows/**`.
- **Authority:** owner/GitHub admin. **Inputs:** actual check names.
- **Live state (2026-10-11, read-only):** `master` protection **absent** (404),
  rulesets **`[]`** → **PROPOSED — NOT ACTIVE**.
- **Verification:** a direct push to `master` is rejected; a PR requires review.

## C. POP-001 authorization (unlocks: acquisition + admission screening)
- **Authority:** `F2-IMPL-AUTH-028` (Rob, 2026-10-07) — "Population acquisition and
  admission screening", target "≥300 admitted task pairs", "If fewer than 300
  survive … STOP".
- **Evidence:** the authorization record + pinned source revisions + integrity/
  licence checks + an acquisition manifest. **Admission ≠ analyzability.**
- **Verification:** `f2_population_acquire.acquire` / `f2_registration.build_manifest`
  pass their guards with a valid POP-001 record. **Does NOT authorize EXPL/CONF.**

## D. Statistical / confirmatory + stage authorizations
- **EXPL-001:** `F2-IMPL-AUTH-024` (exploratory T/X). Requires an approved screening
  population; makes no confirmatory claim.
- **CONF-001:** requires the **empirical** π_d/δ (currently NOT ESTABLISHED) to power
  the design; exact McNemar, two-sided, α=0.05, power 0.80, `n=max(300, required_pairs)`,
  95% Clopper-Pearson. **N=300 is not by itself sufficiency.** Separate authorization.
- **Q10:** already gated by `CalibrationAuthorization` (`F2-IMPL-AUTH-010`); separate
  authorization; not equivalent to the signed verifier.

---

# Appendix J — Trust-root provisioning ceremony + stage authorization templates

## J.1 Production trust-root provisioning (owner-executed; NOT performed here)
1. **Approver authority** (`REQUIRES OWNER INPUT`): the person authorized to approve
   production F2 operations, recorded in the authority log.
2. **Out-of-band identity** (`REQUIRES OWNER INPUT`): how that identity is verified
   independently of the repository/dev process.
3. **Key custody** (`REQUIRES OWNER INPUT`): private signing key held OUTSIDE the
   repo and the ordinary dev process; only the **public** key is provisioned.
4. **Provision:** place `{ "signers": { "<key_id>": "<base64 ed25519 public key>" } }`
   at `qwen_train/authorization/trust_root.json` (or set `F2_AUTHORIZATION_TRUST_ROOT`).
   Public keys only — never a private key.
5. **Rotation / revocation / expiry / compromise / recovery:** publish new `key_id`s;
   remove compromised keys; records carry `not_before`/`not_after`; revoke via the
   record's `revoked`/`superseded_by` and the verifier's `revoked_ids`.
6. **Verify (no secrets):**
   `python -c "from qwen_train import f2_authorization as a; print(sorted(a.load_trust_root().keys()))"`
   then verify a genuine owner-signed record is accepted and a self-signed one is
   rejected (`tests/test_f2_authorization.py`).
7. **Acceptance evidence:** trust-root file present with the owner's public key; a
   genuine owner-signed record verifies; missing/invalid root denies. **The trust
   root is NOT established until this ceremony is completed out of band.**

## J.2 Stage authorization request templates (leave approval/signature to the owner)
- **POP-001** — stage `POP-001`, operations `["acquire","register"]`, authority
  `F2-IMPL-AUTH-028`; bind pinned source revisions + integrity/licence evidence.
  **Does not** permit EXPL/CONF/Q10.
- **EXPL-001** — stage `EXPL-001`, operations `["exploratory_run"]`, authority
  `F2-IMPL-AUTH-024`; screening population; **no confirmatory claim**.
- **CONF-001** — stage `CONF-001`, operations `["confirmatory_run"]`; requires the
  **empirical** π_d/δ (NOT ESTABLISHED) + the approved design (exact McNemar,
  two-sided, α=0.05, power 0.80, `n=max(300, required_pairs)`, 95% Clopper-Pearson).
- **Q10** — separate `CalibrationAuthorization` (AUTH-010); **not** the signed verifier.

Each record is verified by `f2_authorization.verify_authorization` (stage/scope/
digest/expiry/revocation/signature) **before** any protected side effect. Approval
and signature remain the owner's.

---

# Appendix K — Single-maintainer governance model (owner decision, 2026-10-11)

**Owner decision:** this is a **single-maintainer project**. Independent human
review is **currently unavailable**, so the GitHub required-approval count remains
**0** unless and until a legitimate independent reviewer with appropriate
permissions becomes available.

## K.1 Live GitHub configuration (verified, not intended)
Ruleset `protect-master` (id 24867614) is **ACTIVE** with rules `deletion`,
`non_fast_forward`, `pull_request`, `required_status_checks`;
`required_approving_review_count = 0`, `require_code_owner_review = false`,
`dismiss_stale_reviews_on_push = true`; required checks = `test`,
`sota-enforcement`, `frontend (organism-console)`, `frontend (start-console)`,
`Analyze (python)`, `Analyze (actions)`, `Analyze (javascript-typescript)`.
Live collaborators = `Rob2469335` only.

## K.2 Safeguards that DO apply (single-maintainer)
- **PR-only changes** to `master` + **deletion** and **non-fast-forward**
  protection + the **verified required status checks** above.
- Deterministic authorization tests (fail-closed) in `tests/test_f2_authorization.py`.
- Signed-authorization evidence (stage/scope/digest/expiry/revocation).

## K.3 Explicit limitation — NOT equivalent to independent human review
Automated checks, CI, AI review, self-review, and **signed authorization evidence
are NOT equivalent to independent human review**. With one maintainer, a PR can be
authored and merged by the same person; **`required_approving_review_count` stays 0**
deliberately (setting it to 1 would make all merges impossible and is not a
safeguard). This is an **explicit governance limitation**, not a silently satisfied
requirement. **No reviewer, identity, approval, signature, or trust relationship is
fabricated.** CODEOWNERS (`@Rob2469335`) does **not** enforce independent review
while no independent code owner exists, and is not active remote protection until
its contents are on `master`.

## K.4 Recommended (separate authorization required)
If an independent reviewer is added: set `required_approving_review_count: 1` +
`require_code_owner_review: true` and commit `.github/CODEOWNERS`. This requires
separate authorization (live GitHub settings change).
