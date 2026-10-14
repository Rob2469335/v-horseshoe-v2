# F2 / S8 — Offline-Evaluation Isolation: Finding, Smallest Remediation, Verification Plan

**Status: APPLIED (ACL) + VERIFIED (controls) — 2026-10-13.** The additive
outbound deny of §6 was authorized and applied by the operator; the §7 procedure
was executed. See §10 for what was measured, what is established, and what
remains. Sections 1–9 are retained unchanged as the record of the read-only
investigation that preceded the change.

**Prepared:** 2026-10-13 from live read-only host inspection plus existing
recorded evidence.

---

## 1. Verified state

| Item | Value |
|---|---|
| HEAD == `origin/agent/q9-classification-sota` | `52fdecc0` (divergence `0 0`) |
| PR #33 | OPEN, unmerged, auto-merge disabled |
| `F2-S8-VM1` | `Off`; Gen 2; NIC `Network Adapter` on `F2-S8-NAT` (Internal); MAC `00155D56D603`; no IP while Off |
| Switches | `F2-S8-NAT` (Internal), `F2-Internal-Switch` (Internal), `Default Switch` (Internal) — **no external/NAPT switch bound to a physical NIC** |
| NAT | `F2-S8-NAT` = `10.73.0.0/24`, `Active=True`; `WinNat` service Running |
| Host vEthernet (F2-S8-NAT) | `10.73.0.1/24` + fe80; IPv4 **Forwarding Disabled** |
| Host default route | Wi-Fi `192.168.86.1` (ifIndex 13) |

## 2. Source and freshness of the internet-connectivity evidence

The reported `1.1.1.1:443` connection and `example.com` HTTP 200 come from the
**S8 session 4** entry:

- Source: `WORK_LOG.md` "2026-10-10/11 (S8 session 4) — S8 infrastructure/isolation validation: PASS"; checkpoint `C:\Users\rober\F2_EVIDENCE\s8_progress.md` (mtime **2026-10-11 00:07:55**).
- VM identity: `F2-S8-VM1`, guest `10.73.0.2`.
- Configuration at the time: after the owner's scoped Avast network-trust fix for the S8 subnet.
- Recorded results: `TCP CONNECTED (1.1.1.1:443, 8.8.8.8:443)`; `HTTPS https://www.example.com/ -> HTTP 200 / 577 bytes`; prohibited paths `10.72.0.1:8099` and `192.168.86.1:443` **TIMEOUT**.

**Freshness:** the *observed connectivity* is `HISTORICALLY CLAIMED` (2026-10-11;
not re-probed here — no VM start was authorized). The *configuration that produces
it* is `PROVEN IN CURRENT REVISION`: the extended ACL and NAT read now are
unchanged and still permit public egress (below).

## 3. The actual network boundary

**Guest egress is governed by the Hyper-V extended ACL on the VM adapter** (11
rules; higher weight wins):

| Weight | Action | Remote | Protocol |
|---:|---|---|---|
| 900 / 901 | Allow | `10.73.0.1` | TCP / UDP |
| **300 / 301** | **Allow** | **`0.0.0.0/0`** | **TCP / UDP** |
| 800 | Deny | `10.0.0.0/8` | ANY |
| 700 | Deny | `192.168.0.0/16` | ANY |
| 600 | Deny | `172.16.0.0/12` | ANY |
| 500 | Deny | `169.254.0.0/16` | ANY |
| 400 | Deny | `10.72.0.0/24` | ANY |
| 1 | Deny | ANY | ANY (outbound) |
| 1 | Deny | ANY | ANY (inbound) |

- **Internet is permitted** (`Allow 0.0.0.0/0 TCP/UDP` at weights 300/301 outrank
  `Deny ANY` at weight 1) and is translated by `F2-S8-NAT`. — `PROVEN IN CURRENT REVISION` (configuration).
- **`10.72.0.0/24` is denied** (weights 800 and 400) and the host has no NAT for
  `10.72.x`; session-4 timeouts are *consistent with* but **not proof of** this.
  — `SUPPORTED`.
- **Host `F2-Q9-outbound-nonlocal-deny-v4/v6`** are **account-scoped**
  (`LocalUser = S-1-5-21-…-1008`, the F2 arm account), not host-wide — which is
  why the interactive host still has internet. They govern the **F2 clean-room
  arm**, not the S8 guest path. — `PROVEN IN CURRENT REVISION`.
- **Whether the guest currently reaches the internet** is `SUPPORTED` (config
  permits it; session-4 observed it) but not re-measured.

## 4. Finding

The current `F2-S8-VM1` network state is the **acquisition/build** boundary
(internet permitted; private ranges denied). The S8 package's **offline
evaluation** boundary requires **no internet** (`F2_S8_EXECUTION_AND_PILOT_PACKAGE.md`
§1: offline evaluation — *"no internet, no secrets, pinned inputs only"*). The two
are not the same configuration, and the package recommends **one VM, sequential
phases** (§2). Therefore the offline-evaluation boundary is **not satisfied** by
the current state — matching the reconciliation record's gate 4b
(`GOVERNANCE GAP`).

## 5. Channel and isolation can coexist — they do not conflict

PowerShell Direct and Guest Service Interface operate over the **Hyper-V VMBus**,
not the guest's TCP/IP stack:

- PSDirect connects to the guest VM Session Service (`vmicvmsession`) over a
  Hyper-V socket (session-3 root-cause, `PROVEN`).
- `Copy-VMFile` (GSI) also uses VMBus.

Therefore an ACL that denies **all** guest IP egress does **not** affect request
delivery, the guest executor, or artifact retrieval. Tooling (Python + repo +
pinned wheelhouse) is delivered over GSI/VMBus (or a read-only VHDX), so **no
network access is needed** to satisfy the offline boundary. **No conflict; no
owner decision is needed to reconcile the channel with isolation.**

## 6. Smallest compliant remediation (not applied)

**Target:** deny guest public egress during evaluation while leaving the VMBus
channel intact and the change fully reversible.

**Recommended (one additive ACL rule, reversible):** add an outbound rule

```
Direction  Outbound
Action     Deny
Remote    0.0.0.0/0
Protocol   ANY
Weight     between 301 (the public allow) and 900 (the gateway allow), e.g. 350
```

This denies public egress, retains the `10.73.0.1` gateway path if a phase needs
it, and is removed by `Remove-VMNetworkAdapterExtendedAcl` with the same fields.
**No NAT, switch, route, forwarding, firewall, or integration-service change is
required**; the `F2-S8-NAT` NAT may remain because the ACL prevents the guest
from using it.

*Alternative (two removals):* remove the two `Allow 0.0.0.0/0` rules (weights
300/301), leaving `Deny ANY` (weight 1) as the effective default. Equally
reversible, but it also removes the build-phase public allow, so it suits a
permanently-offline guest rather than a phase toggle.

**Why not remove the NAT:** removing/recreating `F2-S8-NAT` is a host network
change with wider blast radius and no advantage over the ACL edit.

**Authority:** the ACL is the **approved 11-rule set** (D4 / `f2_s8_acl.ps1`
baseline). Changing it — even additively — is **`REQUIRES AUTHORIZATION`**. This
plan does not apply it.

## 7. Verifiable isolation procedure (for execution under authorization only)

**Preconditions**
1. Owner authorization recorded for the ACL change and for the Run-2 pilot.
2. `F2-S8-VM1` started (authorized), PSDirect session available (D5).
3. The offline ACL state applied and its read-back captured.

**Read-only configuration checks (host)**
- `Get-VMNetworkAdapterExtendedAcl -VMName F2-S8-VM1` → the expected rule set,
  including the public-egress deny at the expected weight.
- `Get-NetNat` → `F2-S8-NAT` prefix; `Get-NetIPInterface` → forwarding state.
- Record all outputs to a retained file with a SHA-256.

**Positive control (must be reachable when NOT isolated / for the channel)**
- A VMBus operation succeeds: a PSDirect session opens and `whoami` returns the
  expected guest principal. This proves the *channel*, not the network.

**Negative controls (must fail when isolated)**
- From inside the guest: TCP to a public literal IP (`1.1.1.1:443`, `8.8.8.8:443`)
  must **not** connect; DNS resolution must **not** succeed; a route to
  `10.72.0.0/24` must **not** be usable.
- **A timeout is not proof.** A denial is accepted only as a firewall *refusal*
  (`default_egress_probe` classifies `denied` only on a WSAEACCES-style refusal),
  and the **mandatory negative control** must be `permitted` (a real loopback
  listener on `127.0.0.1:1`; see the reconciliation doc §6).
- Run `python -m qwen_train.f2_isolation` **inside the guest** and verify the
  verdict independently host-side.

**Expected result**
- Public egress `denied`; `10.72.0.0/24` unusable; channel reachable; loopback and
  negative control `permitted`; verdict `satisfied=true` from
  `verify_isolation_attestation`. Until that verdict is independently recomputed,
  isolation is `NOT ESTABLISHED`.

**Rollback**
- `Remove-VMNetworkAdapterExtendedAcl -VMName F2-S8-VM1 -Direction Outbound -Action Deny -RemoteIPAddress 0.0.0.0/0 -Protocol Any -Weight 350` (or re-apply the exact 11-rule baseline from `f2_s8_acl.ps1`).
- Verify the baseline rule count and fields read back byte-for-byte.

**Evidence retention**
- Configuration capture + SHA-256; the in-guest `f2_isolation` attestation JSON +
  its recomputed digest; the independent host-side `verify_isolation_attestation`
  verdict. Retain under the trusted store namespace once that is bound.

## 8. Owner decision still required

Two independent grants remain, neither of which this document supplies:

1. **The offline-evaluation ACL change** on `F2-S8-VM1` (additive public-egress
   deny) — a change to the approved ACL set.
2. **The Run-2 / `F2_S8_EXECUTION_AND_PILOT_PACKAGE.md` §8 pilot authorization** —
   the five-task feasibility pilot.

The Q9 attestation of `F2-Isolation-VM` is independent and is **not** a
prerequisite for, nor evidence about, `F2-S8-VM1`.

## 9. What this document does not authorize

No VM lifecycle change; no ACL/NAT/firewall/route/switch/forwarding change; no
guest session; no credential use; no tooling delivery; no network probe; no Q9
attestation; no S8 base/gold execution; no Q10/Q12/Q13; no confirmatory F2; no
receipt-key operation. Every such action is `REQUIRES AUTHORIZATION`.

---

## 10. Results (2026-10-13, applied and measured)

**ACL change (§6), applied exactly as recommended.** One additive rule only:

    Outbound | Deny | RemoteIPAddress 0.0.0.0/0 | Protocol ANY | Weight 350 | Stateful False

Read back twice: 12 rules. Diff against the 11-rule baseline: **1 added,
0 removed, 0 modified**, weight 350 sitting between the public allows (300/301)
and the gateway allows (900/901), so the deny outranks the public allow while
`10.73.0.1` is preserved. NAT, switch, routes, forwarding, host firewall
profiles and integration services compared field-by-field before/after: all
unchanged. Hashes in
`C:\Users\rober\F2_EVIDENCE\s8\offline_iso_2026-10-13\`.

**Procedure (§7), executed.** Positive control: PowerShell Direct opened,
guest principal `DESKTOP-H6A0HQS\rob3`. Mandatory loopback control: a real
`TcpListener` bound to `127.0.0.1:1` for the whole run — `permitted`. Negative
controls: `1.1.1.1:443`, `8.8.8.8:443`, `93.184.216.34:80`, `93.184.216.34:8080`
all `timeout`; UDP `8.8.8.8:53` `silent` (one real datagram); DNS declared but
not used as proof. Host independently still reaches `1.1.1.1:443` and
`8.8.8.8:443`, so the guest's timeouts are guest-specific.

**Verifier reconciliation.** A silently-dropping ACL produces `timeout`, which
the frozen contract classifies `unknown` and can never satisfy — the §7
"expected `satisfied=true`" was unreachable by construction for this enforcement
mechanism. Fixed additively (§4 of the owner's 2026-10-13 instruction): a
denied-required dimension observed only as `timeout`/`silent` is satisfied only
when the HOST supplies the live ACL capture, it hashes to the digest the
attestation claims, its effective rule for the literal target is an explicit
`Deny`, and the negative control is `permitted`. Such dimensions are reported in
`enforcement_backed_dimensions`, never in `satisfied_dimensions`. See
`tests/test_f2_isolation_enforcement_backed.py` (40 tests).

**Outcome.** `http`/`https`/`tcp`/`udp` = enforcement-backed; `proxy` and
`alternate_interface` = policy-asserted under the identified Hyper-V ACL;
`loopback` and `required_service` = observed `permitted`; **no dimension failed**.
`ipv6` is `unavailable` — the guest holds only link-local `fe80::/64` and has no
IPv6 default route (the host has no global IPv6 either), so there is no IPv6
channel to test. All seven `ACCEPTANCE` criteria of
`EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md` §NETWORK are met.

**Limitation.** The frozen verifier still returns `satisfied=false`, because it
counts `unavailable` as `unproven`, while that authority states a dimension with
no global IPv6 is *"vacuously satisfied, but visibly not proven"*. The verifier
was **not** changed to close this gap: altering a fail-closed control to make a
gate pass is prohibited by the governing instruction. Recorded as an open policy
decision, not resolved unilaterally.

### 10a. Owner ruling (2026-10-13): HOLD

Asked to resolve the discrepancy, the owner ruled that the pilot **holds**:

> Option 2 is the correct decision: hold the five-task pilot until the IPv6
> policy discrepancy is formally resolved. The current verifier must remain
> unchanged, and no F2 task should execute while the applicable §6 gate remains
> unsatisfied.

The ruling states the reasoning explicitly, and it rejects the inference this
document's §10 could otherwise invite:

* **An unavailable IPv6 channel is not evidence that IPv6 traffic is blocked.**
  The guest having no global IPv6 address and no IPv6 default route shows there
  is no *path*; it does not show that a path would be *denied*.
* A policy that permits a condition to be treated as vacuously satisfied does
  **not** automatically override a verifier that reports failure.
* The policy, the verifier, and the authorization record must agree on the
  permitted action before execution resumes.

Consequences, all in force:

1. **Evidence preserved** — the frozen verifier, the capture, the attestation
   and the control results are retained unchanged under
   `C:\Users\rober\F2_EVIDENCE\s8\`.
2. **Discrepancy recorded** — IPv6 is `unavailable`; denial of IPv6
   connectivity is `NOT ESTABLISHED`; the host also lacks global IPv6.
3. **An explicit owner decision is required** — either that the existing
   authority language permits proceeding despite `satisfied=false`, or a
   narrowly scoped waiver, or a formally authorized verifier change.
4. **The gate is enforced** — the five-task pilot does **not** run. Resume only
   after that decision is recorded and §6 is demonstrably satisfied.

**Status: HOLD — five-task pilot not authorized to proceed under the current
unresolved gate.**

The conflict itself is isolated, quoted verbatim, and offered as three mutually
exclusive paths in **`docs/EXPERIMENT_J_F2_S8_IPV6_GATE_DECISION_RECORD.md`**
(draft, awaiting the operator). That record is the single artifact the owner
needs; this document no longer carries the analysis.
