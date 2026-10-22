# F2 Q9 Isolation Qualification — Bounded Execution Runbook (FOR OWNER REVIEW)

**Status:** PREPARED, NOT EXECUTED. Nothing in this file has been run. It is an
operational plan for the owner's explicit authorization, not an authority record
and not a claim that Q9 is qualified.

**Prepared:** 2026-10-12 · from live host/repo inspection (no host control applied,
VM not started, trust root not created, nothing downloaded).

---

## 0. Live baseline this runbook is built on (`PROVEN`, this session)

| Item | Live value |
|---|---|
| Repo HEAD == origin/master | `2aad3d1d81ea3ffff7df2628afb9800e9c93cbf3` |
| Reconciled population | 33,967 raw → **33,770** union → **9,268** metadata-eligible → **3,870** evaluator-compat → **3,828** registered; admitted **0**, analyzable **0** |
| `F2-Isolation-VM` | **Off**; Gen2; 4 GB; 2 vCPU; auto-checkpoints **Off**; Secure Boot on; vTPM on |
| VM NIC | **exactly 1** on `F2-Internal-Switch`; MAC `00155D56D601`; no IP while Off |
| VM disk | `C:\Users\rober\F2_VM\F2-Isolation-VM-os.vhdx` (17.69 GB) — guest installed + OOBE completed |
| Host vEthernet | `10.72.0.1/24`, no default gw, no DNS |
| Trust root | **absent** (`qwen_train/authorization/trust_root.json` does not exist) |
| Preflight | **BLOCKED**, 17 findings (11 operator-env, 1 privileged-host, 4 external-evidence, 1 authz, 2 not-executed); **16 IMPLEMENTED** |
| Isolation/readiness/ACL tests | **142 passed** (this session) |
| Reconciliation tests | **7 passed** (this session) |

---

## 1. F2 arm identity and its isolation from the privileged host account

**Arm identity.** The untrusted arm (agent + task code) executes **inside the
Hyper-V guest** `F2-Isolation-VM`, whose only network identity is
`10.72.0.2/24` on `F2-Internal-Switch`. The protected state (model, Qdrant,
evaluator, receipts) stays on the host behind the narrow model gateway.

**How it is isolated from the interactive host account:**
- The guest is a **separate OS + separate security principal**; the operator's
  interactive, privileged host account never enters the guest.
- Hyper-V integration services (Guest Service Interface, PowerShell Direct,
  Data Exchange, VSS, Time Sync) are **disabled** — no clipboard/KVP/host-drive
  side channels (`EXPERIMENT_J_F2_VM_ISOLATION.md`, `PROVEN`).
- The guest has **no default gateway and no DNS** — by topology it has no route
  off the internal switch.
- Exactly **one** stateful allow exists (below); everything else is denied both
  directions.

**Host account isolation — OPEN ITEM (must be stated, not assumed):** the
provisioner `f2_vm_provision.ps1` takes **no guest-user/credential parameters**
(`PROVEN` — grep of the script). The attestation *records* `executing_user`,
but **which principal the guest-side probe runs as is `NOT ESTABLISHED`** from
the repository alone. Before executing, the owner must confirm the guest runs the
arm under a **non-privileged guest account** distinct from any host credential.
If the guest currently auto-logs-on an admin, that account must be de-scoped as
part of this plan.

---

## 2. The host control that enforces default-deny egress

**Current enforcement is topology, not policy.** The internal switch + no-gateway
gives *no route*; it is not a *firewall policy*. The attestation's
policy-asserted dimensions (alternate-interface, interface inventory) are
properties of an **enforced** rule, which the repository cannot impose
(`f2_isolation.py:310` — "NOT observer-provable unprivileged … a property of the
ENFORCED policy").

**Control to apply (scoped to the arm's network identity, NOT the operator's
interactive account):** Windows Defender Firewall **outbound default-deny** for
the guest/ARM identity, mirroring the already-authorized rule baseline in
`qwen_train/f2_s8_acl.ps1` (the 11-rule pattern: explicit stateful allow to the
model gateway `10.72.0.1:8099/TCP`; deny `10.0.0.0/8`, `192.168.0.0/16`,
`172.16.0.0/12`, `169.254.0.0/16`, `10.72.0.0/24` (peer), IPv4+IPv6; deny-all
outbound and deny-all inbound as terminal rules).

| Aspect | Scope |
|---|---|
| **Scope** | The guest's `10.72.0.2` identity (and the host vEthernet `10.72.0.1` serving side). **Explicitly NOT** the operator's interactive account, so normal host/Git/network use is untouched. |
| **Persistence** | Persistent firewall rule (survives reboot); also mirrored inside the guest so the *guest-observed* refusals are policy-backed. |
| **Verification** | (a) `Get-NetFirewallRule`/stored-ACL query confirms the rules; (b) `python -m qwen_train.f2_isolation` `run_egress_probes` observes refusals on http/https/tcp/dns/ipv6 + positive loopback/required-service + **positive negative-control**; (c) `verify_isolation_attestation` independently recomputes the verdict. |
| **Rollback** | Save the pre-change rule set: `Get-NetFirewallRule | Export-NetFirewallRule`. Rollback = re-import that snapshot (or `Remove-NetFirewallRule` for the named F2 rules only). VM-side rollback = `qwen_train/f2_vm_rollback.ps1` (exists). Nothing else is touched. |

**The one action I will not take without you:** applying this host/guest firewall
policy. It is a privileged security-boundary change (`PRIVILEGED HOST` preflight
finding) and is explicitly outside my authority.

---

## 3. Media availability (no download performed)

| Candidate | Path | Size | Notes |
|---|---|---|---|
| Golden OS VHDX | `C:\Users\rober\F2_EVIDENCE\s8\golden\s8-golden-os.vhdx` | 17.69 GB | S8 golden disk; same size as the existing F2 VM disk |
| Official Windows eval ISO | `C:\Users\rober\Downloads\26300.9457.…CLIENTENTERPRISEEVAL…x64FRE_en-us.iso` | 7.66 GB | official eval media; **hash not yet computed** |
| Existing F2 VM disk | `C:\Users\rober\F2_VM\F2-Isolation-VM-os.vhdx` | 17.69 GB | already attached; guest OOBE-complete |

**Both media already exist locally — nothing was downloaded.** However:
- **Reprovisioning is likely NOT needed**: the VM already exists with correct
  topology and an OOBE-complete guest. The normal Q9 path is *start + enforce +
  attest*, not *recreate*. `-Execute` on the provisioner would **recreate** the
  VM — so it must **not** be used unless the owner decides to rebuild.
- **Official Windows eval ISO — hash computed (read-only, this session):**
  - Path: `C:\Users\rober\Downloads\26300.9457.260913-1737.26h2_ge_release_svc_refresh_CLIENTENTERPRISEEVAL_OEMRET_x64FRE_en-us.iso`
  - SHA-256: `BC3F24086EBADC94489066B5AD78089E2CF5C3491E90E790BB81A2B199C10E38`
  - Size: 8,225,329,152 bytes
  - Owner must compare this against a trusted Microsoft reference before using it
    as reprovision media (a local hash records what is present; it does not by
    itself establish provenance).
- **Golden OS VHDX** (`s8-golden-os.vhdx`, 17.69 GB) and the **existing F2 VM
  disk** (`F2-Isolation-VM-os.vhdx`, 17.69 GB): **hash `NOT ESTABLISHED`** —
  not computed (secondary path only; the preferred path starts the existing VM
  and needs no media). Record `Get-FileHash -Algorithm SHA256` if a rebuild is
  chosen.

---

## 4. Exact VM changes, topology, and recovery (planned, not executed)

**Preferred path (VM exists) — 3 steps, owner-authorized:**
1. **Apply** the default-deny firewall policy (§2) — *privileged; the request below*.
2. **Start** the existing VM: `Start-VM -Name F2-Isolation-VM` (it is Off).
   Expected topology after boot: guest `10.72.0.2/24`, no default gw, no DNS;
   **one** allow `10.72.0.2 → 10.72.0.1:8099/TCP` (stateful, idle 1800 s);
   deny-all else, both directions, IPv4+IPv6.
3. **Attest:** run the guest probe (`qwen_train/f2_guest_probe.ps1`) inside the
   guest and `python -m qwen_train.f2_isolation` under the enforced policy →
   produce the `no_egress_attestation` consumed by `f2_readiness`.

**Gateway config expected (from provisioner dry-run, `PROVEN`):**
`F2_GATEWAY_BIND_HOST=10.72.0.1`, `F2_GATEWAY_BIND_PORT=8099`,
`F2_GATEWAY_ALLOWED_CLIENT=10.72.0.2`, `F2_MODEL_UPSTREAM=http://127.0.0.1:8080`.

**Recovery:** `Stop-VM`/`f2_vm_rollback.ps1` restores the VM; firewall snapshot
re-import restores host policy. The provisioner itself refuses to run without
exactly one legitimate media source and never downloads (`PROVEN`).

---

## 5. How Q9 attestation independently proves isolation (not config reporting)

`qwen_train/f2_isolation.build_attestation` / `verify_isolation_attestation` do
**not** trust reported settings. They:

1. **Open real sockets** in `default_egress_probe` — the only place a socket is
   opened, an observer that creates no rule and changes nothing. `denied` is
   recorded only when the network stack actually refuses.
2. **Probe both a name and a literal IP**, so a DNS failure is reported
   `dns_unresolved`, never mistaken for egress denial.
3. **Require every `DENIED_REQUIRED` dimension** (http, https, tcp, dns, ipv6,
   alternate-interface) to be observed-and-refused, and every
   `PERMITTED_REQUIRED` dimension (loopback, required_service) to connect.
4. **Demand a positive negative-control**: if the control is not `PERMITTED`, the
   entire attestation is invalidated (`f2_isolation.py:653`) — a probe that
   cannot distinguish permit from deny proves nothing.
5. **Recompute the verdict** in `verify_isolation_attestation` from the raw probe
   records; the caller's verdict is never trusted. It binds the result to
   `arm_id / rollout_id / workspace / executing_user / interpreter_sha256 /
   policy_sha256`.
6. **Separate observed from policy-asserted**: dimensions an unprivileged observer
   cannot prove are labeled policy-asserted and require the *enforced* policy —
   which is exactly why §2's firewall step must precede attestation.

**Result:** the attestation is an independent re-derivation from live socket
observations + the enforced policy digest, not a restatement of VM settings.

---

## 6. Preflight findings: expected to clear vs. must remain blocked

**Expected to clear after §2 + §4 (if execution authorized):**
- `host.egress_enforcement` → enforced (once default-deny is applied).
- `evidence.q9_attestation` → supplied (once probes pass under enforcement).
- `provenance.conversion` → only if the owner *also* records a genuine
  adapter→merged→GGUF conversion step (owner attestation, separate).

**Must remain blocked (owner/external, out of this plan):**
- `evidence.s8_base_gold` — needs the attested guest to run real base/gold.
- `evidence.active_lesson` — needs Q9 + receipt key + admitted population.
- `authz.population_acquisition` — owner authorization record.
- `run.q10_calibration` (Q10), `run.confirmatory_f2` (Q13) — prohibited without
  further authorization; **not part of this plan**.
- 11 `env.*` OPERATOR ACTION findings — owner must set the real model/evaluator
  identities (`SWARM_DISTILLER_MODEL`, `SWARM_F2_EVALUATOR_*`, artifact root,
  etc.). I will not invent them.
- Trust root absence → all protected stages fail closed (§3 of this runbook's
  companion: trust-root ceremony, separate owner action).

---

## 7. What I will do immediately after authorization

1. Apply the scoped default-deny rule (§2) and verify with `Get-NetFirewallRule`.
2. `Start-VM F2-Isolation-VM`; wait for guest boot; run `f2_guest_probe.ps1`.
3. Run `python -m qwen_train.f2_isolation` under enforcement → produce and verify
   the `no_egress_attestation` (§5).
4. Re-run `python -m qwen_train.f2_preflight` and re-classify every finding.
5. Only then proceed to genuine S8 base/gold evidence in the attested guest,
   measure real admission attrition, and prepare the CONF-001 grant scope from
   the actual eligible population — requesting your approval for *that* grant
   separately, still without touching Q10/Q12/Q13.

---

# COMPANION: Trust-Root Signing Ceremony (FOR OWNER REVIEW — NOT EXECUTED)

No signing tool, key material, or trust root exists in the repo
(`qwen_train/authorization/` is absent; `Test-Path trust_root.json` = False).
The ceremony is **entirely owner-operated with external tooling**. I have not
generated a dummy root, signed anything, or printed key material. Every protected
stage (POP-001/EXPL-001/CONF-001) fails closed until this is done.

## T1. Required signer (owner-controlled)
- Algorithm: **Ed25519**. One key per signer, identified by a stable `key_id`.
- Custody: the **private key never enters this repository, the VM, or any
  process I run.** Signing happens on the owner's offline/authenticated system.
- Key id: a non-empty string (e.g. `owner-2026-10`). Optional per-key `epoch`.

## T2. Trust-root file schema (`qwen_train/authorization/trust_root.json`)
```json
{
  "signers": {
    "<key_id>": "<base64-ed25519-raw-public-key>"
  },
  "revoked_key_ids": [],       // key ids denied even if still listed in signers
  "revoked_record_ids": []     // grant ids revoked post-signing, without re-signing
}
```
A signer value may instead be `{"public_key": "<b64>", "epoch": <int>}` to bind a
key epoch (`load_trust_root` returns `(signers, revoked_key_ids, key_epochs,
revoked_record_ids)`). The verifier decodes each key to **exactly 32 raw bytes**
and rejects anything else (`_decode_ed25519_public`).

## T3. Signed grant record schema (what the signer produces)
Required fields (`_REQUIRED`, `f2_authorization.py:121`):
`id, schema, stage, operations, approver, not_before, not_after, protocol_digest`.
Plus the per-operation bindings (`OPERATION_REQUIRED_BINDINGS`):
- `POP-001/register` → `repository_revision`, `population_manifest_digest`,
  `acquired_inputs_digest`
- `EXPL-001` / `CONF-001` `*_run` → `repository_revision`, `experiment_id`,
  `protocol_version`, `arm`
Signature = Ed25519 over `canonical_bytes(record)` (JSON, `sort_keys=True`,
`separators=(",",":")`, `ensure_ascii=False`). Store as
`{"record": {...}, "signature": {"alg":"ed25519","key_id":...,"signer":<approver>,"sig":<b64>}}`.

**One grant authorizes exactly ONE execution** (ledger keys single-use on the
canonical record digest; replay with a different binding is denied).

## T4. Pinning procedure (owner, out-of-band)
1. Owner computes `sha256` of the trust-root file bytes.
2. Sets `F2_AUTHORIZATION_TRUST_ROOT_SHA256=<that hex>` in the runtime env.
3. The pin is **process-controlled** — a consistency check, **NOT independent
   custody**. A correct pin permits; a wrong pin denies; a **missing pin denies**
   on every protected path (`load_trust_root(require_pin=True)`).

## T5. Independent verification (owner, before any grant)
- `python -c "from qwen_train import f2_authorization as A; import json;
  print(json.dumps(A.describe_trust_root(), indent=2))"` — returns **only public
  material** (key ids, epochs, revocation lists, file digest); never private key.
- Owner compares the reported `digest_sha256` and `signer_key_ids` against an
  **independently held** fingerprint (not from this process).
- Then a signed test grant for the target stage must verify end-to-end via
  `require_authorized(stage, operation, ...)`; a revoked/expired/mismatched
  record must be denied.

## T6. Stop conditions (fail-closed, unchanged)
Absent root, missing pin, wrong pin, wrong-length key, revoked key/record,
expired window, scope escalation, digest/binding mismatch, or an unconsumed /
already-consumed grant ⇒ **denied**. No ceremony output I produce can weaken
these checks.
