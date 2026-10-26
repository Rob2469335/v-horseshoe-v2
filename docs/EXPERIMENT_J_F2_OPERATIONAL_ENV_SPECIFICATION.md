# Experiment J / F2 — Operational Environment Specification

**Document:** `docs/EXPERIMENT_J_F2_OPERATIONAL_ENV_SPECIFICATION.md`
**Status:** Authoritative Specification & Configuration Template
**Governing Authority:** `docs/EXPERIMENT_J_F2_OPERATOR_HANDOFF.md` §1.2, `docs/EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md`, `qwen_train/f2_preflight.py`, `qwen_train/f2_readiness.py`, `qwen_train/f2_evaluator.py`.

---

## 1. Overview & Purpose

The F2 execution harness and automated preflight (`python -m qwen_train.f2_preflight`) evaluate 13 operational environment variables: the ten F2 settings of the specification table below, and the three **S8-only** governed inputs (table rows 11–13) that have no F2 fallback and are required by the S8 composition root. These settings configure live evaluation identity, evaluator binding, artifact retention, and evidence emission (and, for the S8 inputs, the governed base/gold execution-state identity).

All 13 settings belong to the preflight category `OPERATOR ACTION REQUIRED`: a missing or malformed setting blocks. They are deliberately kept out of tracked source control so runtime parameters are explicitly and intentionally provisioned for live experimental operations. A preflight finding here is **readiness only** — a green environment check is never authorization to execute.

---

## 2. Specification Table

| # | Environment Variable | Expected Value / Format | Validation Logic | Governing Authority |
|---|---|---|---|---|
| 1 | `SWARM_DISTILLER_MODEL` | `robs4b` | Non-empty string; must match the model alias served by the local model gateway. | `OPERATOR_HANDOFF.md` §1.2; `f2_readiness._check_distiller_identity` |
| 2 | `SWARM_DISTILLER_WEIGHTS_DIGEST` | `3e5ba031f0b50e98450d1575aa19780bc5b4b13f48bf3eca38f3c915a442a337` | 64-character lowercase SHA-256 hex string; must equal the digest of the **adopted served GGUF** `qwen_train/robs4b_q4km_reconverted.gguf` (2,708,804,032 B). The original `qwen_train/robs4b_q4km.gguf` (2,708,803,840 B, `65202f37…`) is preserved but **not served**. | `qwen_train/f2_preflight.py` `SERVED_GGUF_SHA256`; `swarm_os/services/experiment_model_identity.py` `EXPECTED_GGUF_SHA256`; `f2_readiness._check_weights_digest` |
| 3 | `SWARM_F2_EVALUATOR_ID` | `f2_pytest_junit_evaluator` | Must match `qwen_train.f2_evaluator.EVALUATOR_ID` exactly. | `qwen_train/f2_evaluator.py:87` |
| 4 | `SWARM_F2_EVALUATOR_VERSION` | `1.0.0` | Must match `qwen_train.f2_evaluator.EVALUATOR_VERSION` exactly. | `qwen_train/f2_evaluator.py:88` |
| 5 | `SWARM_F2_EVALUATOR_PROCEDURE` | `f2_pytest_junit_identity_v1` | Must match `qwen_train.f2_evaluator.PROCEDURE_ID` exactly. | `qwen_train/f2_evaluator.py:89` |
| 6 | `SWARM_F2_ARTIFACT_ROOT` | Absolute path (e.g. `C:\Users\rober\F2_ARTIFACTS`) | Must be an absolute directory path on local disk; used by `TrustedArtifactStore`. | `qwen_train/f2_governance.py` |
| 7 | `SWARM_F2_ARTIFACT_RETENTION_DAYS` | `365` | Positive integer string; retention horizon for emitted JUnit, ATIF, and outcome records. | `OPERATOR_HANDOFF.md` §1.2 |
| 8 | `SWARM_F2_EMIT_BUNDLE` | `1` | Must be `"1"`; enforces that child execution emits verifiable deterministic bundles. | `qwen_train/f2_protocol.py` |
| 9 | `SWARM_F2_TASK_OUTCOME_REPORT` | Absolute path (e.g. `C:\Users\rober\F2_ARTIFACTS\task_outcome_report.json`) | Destination path where the evaluator writes the task outcome report. | `qwen_train/f2_evaluator.py` |
| 10 | `SWARM_F2_EVALUATOR_IMPL` | Absolute path (e.g. `C:\Users\rober\Projects\v-horseshoe-v2\qwen_train\f2_evaluator.py`) | Must point to an existing, readable file on disk. Preflight computes its SHA-256 digest dynamically (`f2_preflight.py:249-266`). | `f2_preflight.py:249` |
| 11 | `SWARM_S8_ENVIRONMENT_IDENTITY` | Execution-environment identity (non-empty string) | S8-only: **no F2 fallback**. Must be non-empty; read by `f2_s8_compose.load_governance_from_env`. Reported by preflight as `env.s8_environment_identity`. | `qwen_train/f2_s8_compose.py` `S8_GOVERNANCE_ENV` |
| 12 | `SWARM_S8_BASE_STATE_DIGEST` | 64-character lowercase SHA-256 hex | S8-only: **no F2 fallback**. Must be a well-formed digest and must differ from the gold digest. Reported by preflight as `env.s8_base_state_digest`. | `qwen_train/f2_s8_compose.py` `S8_GOVERNANCE_ENV` |
| 13 | `SWARM_S8_GOLD_STATE_DIGEST` | 64-character lowercase SHA-256 hex | S8-only: **no F2 fallback**. Must be a well-formed digest, distinct from the base digest (identical digests cannot describe two execution states). Reported by preflight as `env.s8_gold_state_digest`. | `qwen_train/f2_s8_compose.py` `S8_GOVERNANCE_ENV` |

---

## 3. Secret & Controlled Configuration

### 3.1 Secret Signing Authority (`SWARM_RECEIPT_KEY`)
- **Nature:** High-entropy secret key used exclusively for cryptographic HMAC signing of promotion receipts.
- **Handling:** **NEVER COMMITTED, LOGGED, OR EXPOSED.** Preflight performs a presence-only check (`_env("SWARM_RECEIPT_KEY")`), verifying existence without reading, logging, or returning its value.
- **Location:** Provided in `.env` or session environment during live execution only.

### 3.2 Optional Conversion Record (`SWARM_F2_CONVERSION_RECORD`)
- **Nature:** Path to a verified JSON conversion record generated by `qwen_train.f2_model_provenance.derive_conversion_record`.
- **Handling:** If supplied via environment variable or CLI (`--conversion-record PATH`), preflight parses and validates all 13 required fields and binds the digests against proven artifacts.

### 3.3 The 7 Invariant Child Execution Controls
Preflight check `env.controls` enforces that the following 7 controls are configured with their frozen values (either in `.env` or inherited from the F2 execution adapter):
- `SWARM_AUTONOMY=0`
- `SWARM_EVOLUTION=0`
- `SWARM_GENETIC_MUTATION=0`
- `SWARM_MEMORY_INJECT=0`
- `SWARM_SEMANTIC_CACHE=0`
- `SWARM_NO_TOASTS=1`
- `SWARM_F1_NO_WEB_TOOLS=1`

### 3.4 Provisioning location — RESOLVED: process/session-only for governed execution

`docs/EXPERIMENT_J_F2_OPERATOR_HANDOFF.md` section 1.1 requires the execution
variables to be set in the process environment for the run only, and section 1.2
places only the receipt key in the operator's `.env`. Section 4 of this
specification previously also offered the operator's local `.env` as a location;
a prior session that appended the ten `SWARM_F2_*`/`SWARM_DISTILLER_*` variables
to `.env` was **reverted** as a section 1.1 violation (see `WORK_LOG.md`,
2026-10-14).

**Owner decision (2026-10-15): process/session-only provisioning for governed
execution inputs.** The `.env` option is **withdrawn** for those inputs; `.env`
support is not to be expanded without separate authorization, and `.env` contents
and secrets are never read, printed, or exposed. The implemented behaviour
already matches the decision:

* the S8 governed-loading path reads the **process environment only**
  (`qwen_train/f2_s8_compose.load_governance_from_env`) and refuses, naming each
  missing input, when one is absent; and
* `qwen_train/f2_preflight.py` still self-loads `.env` **without overriding** an
  explicit process variable (`_load_operator_env`) — a non-overriding *diagnostic*
  for the receipt key (section 1.2) and the seven controls (section 3.3). It
  provisions nothing for execution: a `.env`-only value does not satisfy the
  governed execution path.

---

## 4. Operator Configuration Template

When authorized to stage the live operational environment, the following template
is **exported into the execution session (process environment) for the run**. It
is **not** populated into `.env` for the governed execution inputs (section 3.4);
only the receipt key has a separate `.env` provision location (handoff section
1.2):

```dotenv
# ==============================================================================
# EXPERIMENT J / F2 — OPERATIONAL RUNTIME CONFIGURATION
# ==============================================================================

# Distiller model identity and weights digest
SWARM_DISTILLER_MODEL=robs4b
SWARM_DISTILLER_WEIGHTS_DIGEST=3e5ba031f0b50e98450d1575aa19780bc5b4b13f48bf3eca38f3c915a442a337

# Authorized evaluator identity
SWARM_F2_EVALUATOR_ID=f2_pytest_junit_evaluator
SWARM_F2_EVALUATOR_VERSION=1.0.0
SWARM_F2_EVALUATOR_PROCEDURE=f2_pytest_junit_identity_v1
SWARM_F2_EVALUATOR_IMPL=C:\Users\rober\Projects\v-horseshoe-v2\qwen_train\f2_evaluator.py

# Trusted artifact store and retention
SWARM_F2_ARTIFACT_ROOT=C:\Users\rober\F2_ARTIFACTS
SWARM_F2_ARTIFACT_RETENTION_DAYS=365
SWARM_F2_EMIT_BUNDLE=1
SWARM_F2_TASK_OUTCOME_REPORT=C:\Users\rober\F2_ARTIFACTS\task_outcome_report.json

# Optional: Path to witnessed conversion record JSON (when authorized and witnessed)
# SWARM_F2_CONVERSION_RECORD=C:\Users\rober\Projects\v-horseshoe-v2\qwen_train\conversion_record.json

# Execution controls (frozen F2 arm parameters)
SWARM_AUTONOMY=0
SWARM_EVOLUTION=0
SWARM_GENETIC_MUTATION=0
SWARM_MEMORY_INJECT=0
SWARM_SEMANTIC_CACHE=0
SWARM_NO_TOASTS=1
SWARM_F1_NO_WEB_TOOLS=1

# S8-only governance inputs (qwen_train/f2_s8_compose.py S8_GOVERNANCE_ENV):
#   the six S8_* inputs above are read through their established F2 fallback names
#   (S8_ENV_FALLBACKS); these three have NO fallback and MUST be supplied, or
#   load_governance_from_env() fails closed naming each one.
# SWARM_S8_ENVIRONMENT_IDENTITY=<execution-environment identity>
# SWARM_S8_BASE_STATE_DIGEST=<sha256 of the base execution state>
# SWARM_S8_GOLD_STATE_DIGEST=<sha256 of the gold execution state>

# Secret receipt key: provisioned only during live authorized run (never committed)
# SWARM_RECEIPT_KEY=<operator-provisioned-secret>
```
