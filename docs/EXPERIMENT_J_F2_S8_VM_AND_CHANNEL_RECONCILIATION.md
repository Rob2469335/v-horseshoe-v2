# F2 / S8 — VM Identity, Host→Guest Channel, and Q9 Negative-Control Reconciliation

**Status: RECONCILIATION RECORD — NOT AN AUTHORIZATION.**
This document resolves contradictions between existing governing documents and
records what remains `REQUIRES AUTHORIZATION`. It authorizes **no** VM start,
guest login, Q9 attestation, task execution, credential use, host security
change, or experimental evidence production. It changes no frozen criterion and
does not declare any gate satisfied.

**Prepared:** 2026-10-13 from live repository/host inspection (no VM was started;
no host control applied; no credential read).

**Precedence:** this record is subordinate to `AGENTS.md`, the Experiment J
authority documents, and the operator's explicit current instruction. Where it
states a decision, that decision requires operator authorization before it
governs anything. It is not an authority source.

---

## 1. Why this document exists

Two Hyper-V guests exist and three documents describe the S8 execution guest
differently. The contradiction must be resolved before any further VM, guest,
isolation, or experimental state change.

| Guest | Live identity (read-only, 2026-10-13) |
|---|---|
| `F2-Isolation-VM` | Id `46785b1a-f42c-4efc-b543-a386874db59d`; Gen 2; `Off`; 1 NIC on `F2-Internal-Switch`; GSI/KVP/TimeSync/VSS **disabled**; disk `C:\Users\rober\F2_VM\F2-Isolation-VM-os.vhdx` |
| `F2-S8-VM1` | Id `c5d294f5-5515-46b3-bc80-ddc70dd94fed`; Gen 2; `Off`; 1 NIC on `F2-S8-NAT`; GSI/KVP/TimeSync/VSS **enabled**; differencing disk `…\F2_EVIDENCE\s8\disks\F2-S8-VM1-seed.vhdx` over golden `s8-golden-os.vhdx` |

Both were confirmed bootable in a prior bounded run (`State=Running`,
`Heartbeat=Ok` within 10 s) and returned to `Off`. A boot or attestation of one
guest is **not** evidence about the other.

---

## 2. Resolved: which VM is which

| Role | Guest | Governing source |
|---|---|---|
| **Q9 clean-room isolation qualification** | `F2-Isolation-VM` (10.72.0.2, `F2-Internal-Switch`) | `docs/EXPERIMENT_J_F2_VM_ISOLATION.md` §4; `Q9_OWNER_RUN_CARD.md`; `Q9_QUALIFICATION_RUNBOOK.md`; `F2-IMPL-AUTH-032` |
| **S8 task execution / evidence production** | `F2-S8-VM1` (10.73.0.2, `F2-S8-NAT`) | `docs/F2_S8_EXECUTION_AND_PILOT_PACKAGE.md` §1; operator decisions **D4/D5** (`WORK_LOG.md`, 2026-10-10) |

**They may not be the same VM.** The S8 design states, verbatim:

> "The existing F2 internal network (`10.72.0.0/24`) and the existing
> `F2-Isolation-VM` are **not** part of this design. An internet-reachable build
> runner must never be joined to that network." — `F2_S8_EXECUTION_AND_PILOT_PACKAGE.md` §1

`F2-Isolation-VM` is hardened against host↔guest side channels (integration
services disabled; PowerShell Direct prohibited — `EXPERIMENT_J_F2_VM_ISOLATION.md`
§2, `F2-IMPL-AUTH-032`). `F2-S8-VM1` is a distinct guest created by operator
decision **D4** specifically for S8, with **D5** approving PowerShell Direct and
Guest Service Interface **on it only**.

---

## 3. The contradiction and its controlling authority

**Conflicting statements (exact):**

1. `data/f2_population/reconciliation/capacity_report.json` → `binding_constraint`:
   *"generating it requires the Q9-attested isolated guest (`F2-Isolation-VM`,
   currently Off …)"*.
2. `WORK_LOG.md` (2026-10-12 Q9 entry): *"then S8 base/gold in the attested
   guest"* — referring to `F2-Isolation-VM`.
3. `docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md`
   `F2-IMPL-AUTH-032` (2026-10-09): *"The S8 base/gold execution path cannot be
   driven inside the VM under the current boundaries"* — referring to
   `F2-Isolation-VM`.
4. `docs/F2_S8_EXECUTION_AND_PILOT_PACKAGE.md` §1 and operator decisions **D4/D5**
   (2026-10-10, later): S8 uses a separate disposable guest; `F2-S8-VM1` was
   created and PowerShell Direct approved on it.

**Controlling authority.** The document that *owns* the S8 environment is
`docs/F2_S8_EXECUTION_AND_PILOT_PACKAGE.md` (AGENTS.md §1 authority map:
scientific/execution design). It explicitly excludes `F2-Isolation-VM`. The
operator decisions D4/D5 are later than, and specific to, S8. Therefore:

- Statements 1–3 are **stale with respect to the S8 execution guest**. They
  correctly describe the `F2-Isolation-VM` clean-room boundary and its blocked
  guest channel; they incorrectly name it as the S8 execution guest.
- The S8 execution guest is **`F2-S8-VM1`**.

The generated artifact (1) is produced by `qwen_train/f2_reconcile.py`; correcting
it requires regenerating it, which is **`REQUIRES AUTHORIZATION`** and is not
done here. This record carries the correction instead.

---

## 4. Approved host→guest channel and its limits

| Property | Value |
|---|---|
| Approved channel for **S8** | **PowerShell Direct (VMBus)** + **Guest Service Interface**, on `F2-S8-VM1` **only** (operator decision **D5**) |
| Credential | `C:\Users\rober\F2_EVIDENCE\s8_cred.xml` (operator-created; existence only — the agent must **never** read, copy, print, or transmit its contents) |
| Prohibited | PowerShell Direct / Enhanced Session on `F2-Isolation-VM` (`EXPERIMENT_J_F2_VM_ISOLATION.md` §2; `F2-IMPL-AUTH-032`) |
| Guest-side enablement | `vmicvmsession` started in the guest; network WinRM is **not** the PSDirect path (`s8_progress.md`, session 3 root-cause). Confirmed working (PSDirect session in 1.7 s) |
| Limits | No host shares; no topology/NAT/ACL change; no integration-service change on `F2-Isolation-VM`; the agent may not log into the guest interactively or extract credentials |

**Implemented vs planned (actual code, verified 2026-10-13):**

- **Implemented:** disk-mediated request/result protocol
  (`qwen_train/f2_s8_transport.py`); governed composition + host-side evaluator
  step (`qwen_train/f2_s8_compose.py`); resumable batch driver
  (`qwen_train/f2_s8_batch.py`); channel/record/VHDX readers.
- **Planned / not implemented:** the concrete PowerShell Direct host transport
  (`LiveVMAdapter.transport` and `HostGuestChannel.execute_in_guest` are injected
  seams, not a Hyper-V client) and the **guest-side executor** that consumes a
  request, runs base/gold, and publishes the result. The S8 runner documents its
  in-guest command executor as *eventual* (`qwen_train/f2_s8_runner.py`).

---

## 5. Attestation identity binding

A Q9 attestation is accepted only when it binds an enforcement identity
(`qwen_train/f2_isolation.assess_enforcement_identity`): `arm_id`, `rollout_id`,
`workspace`, `enforcement_scope` (a recognised model), `interpreter_path`,
`interpreter_sha256`, `executing_user`, plus a well-formed `policy_identity` /
`policy_sha256` for any policy-asserted dimension. It is verified independently
by `verify_isolation_attestation`; a caller-asserted verdict is ignored.

**The Q9 attestation binds the `F2-Isolation-VM` clean-room arm identity. It does
not bind, and must not be reused for, `F2-S8-VM1`.**

---

## 6. Correct negative-control procedure

**Defect.** `qwen_train/f2_isolation._negative_control` probes
`127.0.0.1:1`. `verify_isolation_attestation` requires that probe to be
`permitted`. In a guest where nothing listens on port 1, the TCP connect is
**refused** → classified `denied` → the attestation is **unproven**
(`negative_control`), and `test_negative_control_absent_listener_is_not_permitted`
encodes exactly that. The mandatory control therefore requires a **genuine
loopback listener on `127.0.0.1:1`** during the attestation run.

`f2_q9_proposed/run_probes_as_f2.ps1` starts its self-test listener on
`127.0.0.1:18271` — **a different port** — so it does **not** satisfy the
mandatory control.

**Correct, protocol-compliant procedure (no verifier change):** start a real
loopback listener on **`127.0.0.1:1`** for the duration of
`python -m qwen_train.f2_isolation`, e.g.:

```powershell
$l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 1)
$l.Start()      # keep alive for the whole attestation run; stop afterwards
```

Then run the attestation; the `loopback` dimension and `negative_control` must
both read `permitted`.

**If port 1 cannot be bound in the guest** (Windows excluded-port range), the
fix is to make the control port configurable — a change to the frozen probe
protocol and **`REQUIRES AUTHORIZATION`**. Do **not** weaken the verifier,
fabricate a connection, or reclassify a refusal.

---

## 7. Execution gates (separate and non-substitutable)

| # | Gate | Label | Authoritative source | Current evidence | Missing prerequisite | Smallest owner action |
|---|---|---|---|---|---|---|
| 1 | Q9 in-guest attestation of `F2-Isolation-VM` | **REQUIRES AUTHORIZATION** | `EXPERIMENT_J_F2_VM_ISOLATION.md` §4; `Q9_OWNER_RUN_CARD.md` | host-side enforcement prepared; attestation absent | owner console session + guest credential + in-guest tooling + a listener on `127.0.0.1:1` | run `Q9_OWNER_RUN_CARD.md` at VMConnect; hand back `f2_q9_attestation.json` |
| 2 | Host egress enforcement | **PRIVILEGED HOST ACTION** | `OPERATOR_HANDOFF.md` step F; preflight `host.egress_enforcement` | `not_enforced` | default-deny egress policy for the arm identity | apply the policy, then run the probes |
| 3 | S8 host→guest channel | **APPROVED (D5) for `F2-S8-VM1`** | `WORK_LOG.md` D5 | PSDirect worked (1.7 s); GSI enabled | concrete transport + guest executor (**not implemented**) | none host-side; repository work + guest tooling |
| 4 | S8 base/gold execution | **REQUIRES AUTHORIZATION** | `F2_S8_EXECUTION_AND_PILOT_PACKAGE.md` §8; preflight `evidence.s8_base_gold` | 0 pairs | explicit grant to execute base/gold | authorize the S8 execution in `F2-S8-VM1` |
| 5 | Trusted store + evaluator identity | **GOVERNANCE GAP** (values decided, not bound) | `OPERATOR_HANDOFF.md` step J; `WORK_LOG.md` D3 | `SWARM_F2_*` unset at runtime | process-scoped `SWARM_F2_*` env binding | set the `SWARM_F2_*` variables for the run |
| 6 | Confirmatory F2 (Q13) | **REQUIRES AUTHORIZATION** | `EXPERIMENT_J_F2_EXPLORATORY_AUTHORIZATION.md`; SAP §6 | not executed | scientific authorization | out of scope here |

**S8 environment role:** whether S8 base/gold requires a Q9-style isolation
attestation of `F2-S8-VM1` (the session-4 S8 network validation is **not** the
`f2_isolation` attestation), or whether the D4/D5-approved environment suffices,
is **not resolved by any committed authority**. This is a **`REQUIRES
AUTHORIZATION`** decision for the operator. It does **not** require, and must not
be satisfied by, a Q9 attestation of `F2-Isolation-VM`.

`SWARM_RECEIPT_KEY` presence (preflight `env.receipt_key`) is **not**
authorization to use it and is irrelevant to S8 admission.

---

## 8. Evidence required before a task counts as admitted

A task is admitted only when all of the following are durably recorded
(`qwen_train/f2_s8_compose.py`, `f2_evidence.verify_task_evidence`,
`f2_governance.verify_governed_pair`):

- the same authorized task identity (`instance_id`), repository, and base commit;
- a base-FAIL and a gold-PASS **retained** evaluator report + run log, hashed
  under the operator-provisioned **trusted artifact store**;
- evaluator identity + version + implementation digest + procedure + protocol,
  authorized by the registry;
- the required base-fails/gold-passes relationship, re-derived from the retained
  evidence (never the producer's declaration);
- the admission decision appended to the append-only ledger with its evidence
  digest and size.

`admitted` counts one pair per task; retries, sessions, and seeds do not
double-count. `analyzable` remains 0 until the later T/X phase.

---

## 9. Exact next owner actions (in order)

1. **Decide the S8 execution guest and whether Q9 is a prerequisite** (§7 note).
   Until this is recorded, S8 execution must not begin.
2. **If S8 proceeds in `F2-S8-VM1` (the documented default):** complete guest
   tooling (real Python + git + the executor; the session-3 guest had only a
   Python Store shim and no git — re-verify current state), then authorize the
   S8 base/gold run and bind the `SWARM_F2_*` store/evaluator env.
3. **Independently (not on the S8 path):** complete the Q9 attestation of
   `F2-Isolation-VM` per §6, using a loopback listener on `127.0.0.1:1`.

---

## 10. What this document does not authorize

No VM start/stop; no guest login or PowerShell Direct session; no credential
access; no Q9 attestation; no S8 or F2 task execution; no Q10/Q12/Q13; no receipt
key provisioning or rotation; no host security, network, ACL, or
integration-service change; no modification of frozen criteria; no regeneration
of the reconciliation artifacts. Any such action is `REQUIRES AUTHORIZATION`.
