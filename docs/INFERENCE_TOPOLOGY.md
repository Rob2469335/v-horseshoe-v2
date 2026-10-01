# Inference Topology & Model Identity

**Scope:** the two legitimate inference topologies and the model-identity /
configuration-provenance contract enforced by the Experiment J SWE evaluator.

This is an **operational/implementation** document. It records what the
production code does. It grants **no scientific authorization**: F0 scientific
parameters, F1/F2 authorization documents, thresholds, population, `n`, `δ`,
statistical tests, promotion criteria, and evaluator identity/version are
governed elsewhere and are unchanged by anything described here.

Implementation of record:

| Concern | File |
|---|---|
| Preregistered identity + verification | `swarm_os/services/experiment_model_identity.py` |
| Preflight / postcheck enforcement | `swarm_os/services/prompt_repairer.py` (`BenchmarkEvaluator`) |
| Router topology switch | `model_router.py` (`_router_pinned()`) |

---

## 1. Two topologies

Both are supported. Neither is a degraded mode.

### LOCAL

* Selected when `SWARM_ROUTER_PINNED` is **absent**, empty, or `0`.
  `model_router.py:98` is `os.environ.get("SWARM_ROUTER_PINNED", "0") == "1"`,
  and its docstring states the default *"preserves the local heavy/daily
  behaviour unchanged"* — so absent means local.
* `:8079` must be owned by **`llama.exe`** (case-insensitive match on
  `"llama"`).
* **No** SSH tunnel and **no** pin file required.
* The served GGUF is hashed and must exactly match the preregistered artifact.
* The `/props` configuration fingerprint is verified.
* Authenticated router status and boot identity are recorded.

### RUNPOD

* Selected when `SWARM_ROUTER_PINNED=1`.
* Authenticated status must report a **strict boolean** `pinned=true`.
* The environment value and the authenticated router status **must agree**.
* `:8079` must be owned by **`ssh.exe`**.
* Pin configuration **and** the pin fingerprint comparison remain enforced.
* The **same** preregistered model identity is required.

### Fail-closed behaviour

| Condition | Result |
|---|---|
`SWARM_ROUTER_PINNED` invalid/junk | **fail closed** — not silently treated as local |
Environment and authenticated status disagree | **fail closed** |
`pinned` missing / `null` / `"false"` / `0` (non-boolean) | **fail closed** |
Local `llama.exe` endpoint presenting as RunPod | rejected (`ssh.exe` required) |
SSH endpoint presenting as local | rejected (`llama` required) |
Status endpoint unauthenticated / non-200 / unreachable | **fail closed** |

Preflight failure returns no verdict and the arm never starts
(`prompt_repairer.py:323-331`).

---

## 2. Preregistered model identity

**Do not derive these from a running endpoint.** They were recorded in
`docs/RUNPOD_RUNBOOK.md` and `AGENTS_LEGACY.md:6193` before the verification
mechanism existed, and were independently recomputed from the GGUF.

| Property | Value |
|---|---|
model alias | `robs4b` |
GGUF | `qwen_train/robs4b_q4km.gguf` |
size | `2708803840` bytes |
**SHA-256** | `65202F372110DDE854B40CE15DCD1B6AB56A1FE9EA542B84B6A9CC745B242D41` |

Current local runtime configuration (secondary; see §3):

| Property | Value |
|---|---|
`/props` fingerprint | `23408a87ffe0f2cb` |
build fingerprint | `b10107-c0bc8591e` |
context (`n_ctx`) | `16384` |
slots | `1` |

Changing any expected identity value is a scientific change and must appear as
a visible diff in `experiment_model_identity.py`.

---

## 3. Three distinct concepts — not interchangeable

### (a) Model identity

**alias + exact GGUF path/content + SHA-256.** This is the F0-required record
(`EXPERIMENT_J.md:125`, `:144`, `:145`).

The SHA is computed over **the exact artifact the serving endpoint reports in
`/props.model_path`**. There is deliberately **no fallback** to a local file:
if the reported path is not a readable local file, verification fails closed. A
basename match is *not* proof of content — a remote endpoint could serve
different bytes under the authorized filename, and hashing a local copy would
then attest to a model that was never served.

**Consequence:** a remote (RunPod) endpoint whose `model_path` is pod-side and
inaccessible **fails closed**. RunPod identity verification is therefore not
satisfiable until that endpoint exposes its own model digest. No pod-side
hashing is implemented.

### (b) Runtime / configuration fingerprint

`_props` hash over `build_info | model_path | n_ctx | total_slots`
(`experiment_model_identity.props_fingerprint`).

This is a **secondary configuration-integrity** signal: it detects endpoint
drift (context, slot count, build). It is **not** model identity and must never
substitute for the SHA. Verification order guarantees this — alias, then
resolve, then size, then SHA, and only then the fingerprint.

### (c) Topology provenance

`local` vs `runpod`, plus the `:8079` endpoint owner process name and the
router `boot_id`. Recorded as evidence, not used as the model identity.

---

## 4. Provenance persisted to experiment evidence

The SWE rollout record (`data/prompt_repairer_rollouts.jsonl`, appended by
`prompt_repairer._append_rollout` via `_rollout_log_file()`) now carries seven
identity/provenance fields:

* `model_alias`
* `model_gguf_sha256`
* `model_gguf_size_bytes`
* `props_fingerprint`
* `inference_topology`
* `inference_endpoint_owner`
* `router_boot_id`

**Verification performed.** These were confirmed by executing the real
production `_run_preflight()` against the live endpoint and the real
`_append_rollout()` append path, then reading back the serialized line. All
seven fields were present with the expected values. The evidence sink was
redirected to a temporary directory for that check, so the real append-only log
was not written to.

**Current limitation.** Existing historical rollout records predate this
provenance change (they contain none of these fields, and also predate
`inference_endpoint`). **The corpus therefore does not yet contain a genuine
post-change arm record.** The first genuine Experiment J arm will provide that
empirical evidence. No Experiment J learning result is claimed here.

---

## 5. Relationship to other documents

* `docs/RUNPOD_RUNBOOK.md` — RunPod operational procedures only. It remains the
  source of the originally recorded GGUF SHA (§2). Local operation is **not**
  covered there.
* `docs/EXPERIMENT_J.md` — frozen F0 scientific design. **Not modified.**
  Sections 125/144/145 already *require* GGUF-SHA provenance; this document
  describes how the requirement is implemented, it does not alter it.
* `docs/LEARNING_EXPERIMENT_STATE.md` — current learning state. **Not modified.**