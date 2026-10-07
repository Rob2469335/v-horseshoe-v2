# Experiment J / F2 - VM Isolation Boundary

**Scope:** the F2 host/guest isolation boundary only. This document is the
source of truth for the F2 VM topology, network policy, and isolation
validation. It does **not** define any F0 science and does **not** authorize
running the experiment.

**Status (reconciled 2026-10-07):** repository-side isolation is implemented and
tested (F2-IMPL-AUTH-027). The host/guest **VM shell now exists** on this host —
`F2-Isolation-VM` (Generation 2; exactly one NIC on the Internal
`F2-Internal-Switch`; the authorized three-rule extended-ACL baseline; Windows
guest installed and OOBE completed) — **but the guest→host gateway path was
never proven**: the post-fix guest retest was not obtained, there is **no Q9
attestation**, and **no guest-egress property is validated**. The VM topology is
**RECOMMENDED, not authorized for F2 execution** (F2-IMPL-AUTH-025), and F2
execution is **BLOCKED** by required human/external prerequisites
(`docs/EXPERIMENT_J_F2_OPERATOR_HANDOFF.md` §4; `python -m qwen_train.f2_preflight`).
"VM exists" is **not** "VM topology authorized for F2". See
`docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md`
(F2-IMPL-AUTH-025/026/027) for the governing ledger entries.

Baseline revision when this document was written:
`488194d9b50263a237e8a1141e0ccc33aef1a990`.
Reconciled to the 2026-10-07 host audit; repository HEAD
`6a7ad4b990ef55a9febcec65a98dc5ab431244a3`.

---

## 1. Purpose

Keep the untrusted F2 execution environment (P2 backend + model-generated
edits + declared task commands) inside a Hyper-V guest that can reach the host
through **exactly one** capability: the local model API, via a narrow gateway.
The model and all protected state remain on the host, outside the untrusted
boundary.

## 2. Threat model

Assumed hostile: the repository/task code and the model-generated tool calls
executed inside the F2 guest. Goals of the boundary:

- no reachability to Qdrant (`:6333`/`:6334`) or the embedding service (`:8081`);
- no reachability to the LAN, the internet, or arbitrary host services;
- no host credentials, no `.env`, no SSH/Git credentials, no host filesystem
  mapping, no evaluator/reference truth;
- no generic TCP/HTTP proxy surface (the model gateway is not a proxy);
- no side channels (clipboard, KVP/Data Exchange, PowerShell Direct, host drives).

## 3. Architecture (target)

```
HOST
  |
  | local model only            Qwen/llama.cpp  http://127.0.0.1:8080/v1
  ^                             (bound loopback; stays on the host)
  |
  | narrow model gateway        host vEthernet 10.72.0.1:8099/TCP
  ^                             (qwen_train/f2_model_gateway.py)
  |
Hyper-V INTERNAL switch          (no External, no NAT, no uplink)
  ^
  |
F2 VM 10.72.0.2                  (exactly ONE NIC)
  |
  +-- P2 / backend
  +-- F2 task workspace
  +-- untrusted task / tool execution
```

## 4. VM topology and identities

| Property | Intended value | State (2026-10-07 host audit) |
|---|---|---|
| Switch | `F2-Internal-Switch`, type **Internal** | **exists**, Internal; no external switch, no NAT, no bridge — `PROVEN` |
| VM | `F2-Isolation-VM`, **Generation 2** | **exists** (Gen 2; currently `Off`) — `PROVEN` |
| NIC count | **exactly 1** | **exactly 1**, on `F2-Internal-Switch` (DhcpGuard/RouterGuard On, MAC spoofing Off) — `PROVEN` |
| Host vEthernet | `10.72.0.1/24`, **no default gateway**, **no DNS** | `10.72.0.1/24`, IPv4 forwarding **Disabled**, no route via F2 — `PROVEN` |
| Guest | `10.72.0.2/24`, **no default gateway**, **no DNS** | guest install + OOBE completed; guest-side network identity is **GUEST-REPORTED only** — `NOT ESTABLISHED` from the host |
| Secure Boot | On | On — `PROVEN` |
| vTPM | Enabled, local key protector | Enabled, protector present — `PROVEN` |
| Integration services | Guest Service Interface / PowerShell Direct / Data Exchange / VSS / Time Sync **disabled**; Heartbeat + Guest Shutdown **enabled** | matches intended (GSI/KVP/VSS/Time-Sync off; Heartbeat + Shutdown on) — `PROVEN` |

The provisioner declares this boundary; it was **instantiated by the operator**
(`qwen_train/f2_vm_provision.ps1 -Execute`), not by this document, and the
host/guest socket-level enforcement remains **unproven** (no Q9 attestation).
`Get-VM` now returns `F2-Isolation-VM` and `Get-VMSwitch` returns the OS-managed
`Default Switch` (Internal) plus `F2-Internal-Switch` (Internal) — `PROVEN`
(2026-10-07 host audit).

## 5. Network policy (design; provisioner-encoded)

- **Default deny**, both directions, all protocols (TCP/UDP/ICMP/other), IPv4
  and IPv6.
- **Single allow** (parameters verbatim from the provisioner): `Outbound, Allow,
  LocalIPAddress=$GuestIp (10.72.0.2), RemoteIPAddress=$HostGatewayIp
  (10.72.0.1), RemotePort=$GatewayPort (8099), Protocol=TCP, Weight=100,
  Stateful=$true, IdleSessionTimeout=1800`.

**Timeout contract** (`qwen_train/f2_isolation_contract.py`). The real chain is
guest → gateway (8099) → `model_router` (8080) → llama (8079). The **binding
ceiling** is the `model_router` proxy's `httpx.AsyncClient(timeout=300.0)`
(`model_router.py:104`), so the effective end-to-end request ceiling is
`min(router 300, gateway 960, client 900) = 300 s`. The Hyper-V
`-IdleSessionTimeout` is a **silent-gap bound, not a total-request timeout**: the
maximum silent interval between packets is bounded by the upstream compute time
(≤ 300 s), and **1800 s ≈ 6×** that ceiling. `tests/test_f2_isolation_timeouts.py`
proves the ordering, the router ceiling, and the silent-gap derivation, and guards
against a stale `300` returning. **Answer:** *can the model server/router kill an
F2 request before the gateway/client timeout?* **Yes — the router's 300 s total is
the effective cap** (changing it is a model-infra change, `REQUIRES
AUTHORIZATION`).

**F2 request worst case (derived, not measured).** The F2 tool-decision call uses
`local_max_tokens = 512` (`_llm_client.py:437`) on a path with `timeout = 300.0`
(`_llm_client.py:444`); the in-repo comment cites ~73 s at 7 tok/s
(`_llm_client.py:431`), i.e. ≈24–73 s over a 7–21 tok/s range — **well under the
router's 300 s**. The 900 s streaming path (`_llm_client.py:617,633`) is a
different call. **Conclusion: no router-timeout change is required for the
tool-decision path.** Exact worst-case duration on this host is `NOT ESTABLISHED`
(no measured F2 run); if a future F2 call enables the 900 s streaming path, that
is `REQUIRES AUTHORIZATION`.
- Allow weight (`100`) **outranks** the catch-all deny weight (`1`).
- Catch-all deny uses the Microsoft-documented wildcard `-LocalIPAddress ANY
  -RemoteIPAddress ANY` — **`ANY` = all IPv4 AND IPv6 addresses** — for
  `Outbound Deny weight 1` and `Inbound Deny weight 1`, all protocols.
- No allow rule other than the single 8099 flow. No DHCP, no NAT, no default
  gateway, no DNS.

**Microsoft semantics (verified 2026-10-07, Microsoft Learn
`Add-VMNetworkAdapterExtendedAcl`):** the cmdlet has `-Stateful <Boolean>` and
`-IdleSessionTimeout <Int32>` ("a time-out period, in **seconds**"; *not* a
TimeSpan). Extended ACLs are **stateless unless `-Stateful $true` is passed** —
`-IdleSessionTimeout` alone does not make a rule stateful. `-LocalIPAddress` /
`-RemoteIPAddress` accept `0.0.0.0/0` (all IPv4), `::/0` (all IPv6), or **`ANY`
(all IPv4 and IPv6)**.

**Evidence classification.** The policy is **CODE PROOF**: encoded in
`qwen_train/f2_vm_provision.ps1` and statically asserted by
`tests/test_f2_vm_provision_acl.py` (8 cases, no VM needed). It remains
`NOT ESTABLISHED` as live **VM NETWORK PROOF** from the guest: the VM now
exists and the host-side stored-ACL query confirms the authorized three-rule
baseline, but no guest socket probe, IPv6/ICMP test, or Q9 attestation has
produced a passing result, so guest-egress enforcement is unproven. At apply
time the
provisioner prints every stored ACL field (`Direction, Action, LocalIPAddress,
RemoteIPAddress, LocalPort, RemotePort, Protocol, Weight, Stateful,
IdleSessionTimeout`) and **fails closed** unless exactly one outbound Allow
exists, it is `Stateful = $true`, it has a positive idle timeout, inbound and
outbound catch-all denies exist, and the allow weight outranks the deny weight.

## 6. Model gateway

`qwen_train/f2_model_gateway.py`:

- **Allowed routes only:** `POST /v1/chat/completions`, `POST /v1/completions`,
  `GET /v1/models`.
- **Fixed upstream:** default `http://127.0.0.1:8080` (the local model). The
  client cannot set host/port/scheme/URL.
- **Source allow-list:** only the configured guest address.
- **Not a proxy:** no CONNECT/proxy semantics, no redirect following (a 3xx
  upstream is refused with 502), bounded request body (8 MiB), fail-closed 503
  when upstream is unavailable, streaming/SSE preserved.
- **Bind safety:** `main()` refuses `0.0.0.0`/`::`/empty, and refuses to start
  without an allowed client. Intended bind: `10.72.0.1:8099`.
- **No Qdrant/embedding/filesystem/control/evidence routes exist.**

**Evidence:** `CODE PROOF` (`tests/test_f2_model_gateway.py`, 13 cases).

## 7. Governed-F2 runtime suppression (P2, inside the boundary)

When `SWARM_F2_ISOLATION=1`, `swarm_os/app/main.py` does **not start**:
MemoryBridge `watch_loop`/`start_manager_daemon`, the codebase-index self-heal
daemon, external MCP init, the task scheduler, reflection, genetic mutation,
evolution, the autonomy watch-loop, intelligence, eval-tick, Telegram, chess
resume, or system-probe warmup. `f2_p2_environment()` sets
`SWARM_F2_ISOLATION=1` (production path) and `SWARM_CODEBASE_INDEX=0` (all F2
P2). Qdrant-backed tools (`semantic_search`, `remember`, `deprecate_memory`) are
removed from the arm tool surface. A startup assertion fails closed unless
`127.0.0.1:6333` and `127.0.0.1:8081` are **unreachable**
(`EXPECTED_UNREACHABLE`; `REACHABLE_VIOLATION` or `CHECK_ERROR` aborts P2).

**Evidence:** `CODE PROOF` (`tests/test_f2_runtime_guard.py`,
`tests/test_f2_isolation_suppression.py`, 27 focused tests pass).

## 8. Artifact ingress / egress (proposed)

The guest must not reach the host filesystem. Candidate mechanisms:

- **Option A - read-only input ISO.** Host builds an ISO (verified installers,
  wheels, an authorized repo snapshot, immutable inputs); attach read-only;
  every artifact SHA-256-recorded before use; guest verifies hashes. No network,
  no shared filesystem. Smallest surface.
- **Option B - separate output VHDX.** Guest writes only authorized results to a
  small dedicated disk; host mounts it **only while the guest is off**, treats
  all contents as **UNTRUSTED**, never executes recovered files, records
  hashes, caps size.
- **Option C - second network port.** **Rejected as the primary design**: it
  adds attack surface, another host service binding, more ACL complexity, an
  application-auth requirement, and provenance ambiguity. Not chosen.

**Chosen direction (evidence-based).** **Read-only VHDX** for input and a
separate output VHDX for egress, with a SHA-256 manifest on every artifact and an
`UNTRUSTED OUTPUT` classification on egress. Input uses VHDX rather than an ISO
**Correction (read-only truth).** A VHDX is **not** inherently read-only, and
`Mount-VHD -ReadOnly` proves only that the **host-side mount** is read-only — it
does **not** prove the guest sees the disk read-only. A documented Hyper-V
mechanism to attach a VHDX to a **guest** read-only is `NOT ESTABLISHED`, and no
`Add-VMHardDiskDrive -ReadOnly` parameter exists. VHDX remains implemented
(scratch-proven lifecycle) but its **guest-side read-only is `NOT ESTABLISHED`**;
the guest gets a write probe (`f2_guest_probe.ps1 -InputDisk`), and a generic
write failure does **not** prove read-only media.

**Medium comparison (input delivery).**

| Property | VHDX (current) | DVD/ISO (IMAPI2) |
|---|---|---|
| Host construction | `New-VHD`/`Mount-VHD` (done, scratch-proven) | `IMAPI2FS.MsftFileSystemImage` COM |
| Extra software | none | none — **ADK not required** |
| Guest write resistance | **NOT ESTABLISHED** | **inherent read-only medium** |
| Integrity/provenance | SHA-256 manifest inside the volume | SHA-256 of the `.iso` file + manifest inside |
| Operator error risk | mount/format correctness | lower (single artifact, add-only) |
| Automated verification | volume read-back | `Get-FileHash` on the ISO + guest read |
| Compatibility | Hyper-V native | Hyper-V DVD drive / Win11 mounts ISO natively |

**IMAPI2 capability (host, read-only check, 2026-10-07):** `New-Object -ComObject
IMAPI2FS.MsftFileSystemImage` succeeded (`System.__ComObject`) — the Windows-native
path exists **without the ADK**.

**Recommendation (SOTA): DVD/ISO via IMAPI2 for INPUT delivery** — the ISO medium
is inherently read-only to the guest, closing the guest-write concern that VHDX
leaves `NOT ESTABLISHED`; it is Windows-native (no ADK, no download) and gives a
single hashable artifact. The VHDX builder stays for **output/egress** (§ output
VHDX). Implementing the IMAPI2 ISO input builder is the next authorized item
(`REQUIRES AUTHORIZATION`), not done in this static pass. Builder: `qwen_train/f2_input_bundle.ps1`
(plan-only by default; `-Execute` creates the VHDX) with
`tests/test_f2_input_bundle.py`. The input VHDX carries the manifest, whose
`host_reference_utc` seeds the guest clock startup check (§11a).
`REQUIRES AUTHORIZATION` before implementation.

## 9. What the guest needs offline

Guest needs: Windows (installed once from official media), Python 3.14 + the
task toolchain, an authorized repository snapshot, and immutable F2 inputs. It
does **not** need Qdrant, the embedding service, DNS, Windows Update, the
internet, or arbitrary host access during governed execution.

## 10. Host-side services and Avast

Only the model gateway may be reachable from the guest on the F2 vEthernet
(`10.72.0.1:8099`). All other host listeners on that interface must be proven
unreachable from the guest. Windows Firewall and Avast are **not** modified by
this work. If `guest 10.72.0.2 -> host 10.72.0.1:8099/TCP` is blocked,
diagnosis is read-only; the narrowest human action, if proven necessary, is a
single allow for source `10.72.0.2`, destination `10.72.0.1`, TCP, destination
port `8099`. `NOT ESTABLISHED` (no VM to test from).

**Host listener inventory (read-only, 2026-10-07).** A host service is reachable
from the future guest only if it both binds a guest-reachable address AND the ACL
permits it. `127.0.0.1` binds are never reachable from the guest; `0.0.0.0` binds
are reachable at `10.72.0.1` *if* the ACL fails — that is exactly what the guest
probe tests.

| Bind | Port | Process | Reachable via 10.72.0.1? | Reason |
|---|---|---|---|---|
| `127.0.0.1` | 6333/6334 | qdrant | No | loopback-only bind |
| `127.0.0.1` | 12xxx/27xxx | AvastSvc | No | loopback-only bind |
| `0.0.0.0` | 135 | svchost | Only if ACL fails | RPC endpoint mapper |
| `0.0.0.0` | 445 | (SMB) | Only if ACL fails | not observed listening here |
| `0.0.0.0` | 2179 | vmms | Only if ACL fails | Hyper-V VM management |
| `0.0.0.0` | 11435 | svchost | Only if ACL fails | Windows service |
| `0.0.0.0` | 16992/16993 | LMS | Only if ACL fails | Intel LMS (AMT) |
| `0.0.0.0` | 623/664 | LMS | Only if ACL fails | Intel LMS |
| `0.0.0.0` | 49664-49672, 5040 | system | Only if ACL fails | RPC/dynamic ports |

**Guest probe list** (from this inventory): allow `10.72.0.1:8099`; deny/verify
`10.72.0.1:6333,6334,8081` and representative `0.0.0.0` binds (`135`, `2179`,
`11435`). Classification: reachability through the future F2 NIC is `INFERRED`
(no interface exists yet); the ACL is the primary boundary.

## 11. Secure Boot / vTPM

Secure Boot On and vTPM with a local key protector are set by the provisioner
for the Generation-2 VM. Shielded VM / HGS is **not** used (no requirement).
Intel GPU passthrough is **not** attempted: the model stays on the host by
design.

## 11a. Timestamp / clock analysis (source-only)

The F2 timestamps are **not all one clock**. P2 generates its timestamps with
`time.time()` / `time.gmtime()` (epoch/UTC) **inside the P2 process**; once P2
runs inside the guest, those are **guest-clock-originated**. The freeze/host side
generates `freeze_timestamp` on the **host clock**. With Hyper-V Time
Synchronization disabled these are two clock domains. (This corrects the earlier
draft that described every timestamp as simply the host wall clock.)

| Timestamp | File:line | Origin | Clock |
|---|---|---|---|
| `delivery_timestamp` (P2) | `runtime_v2/services/f2_replay.py:342` (`time.time()`) | **guest** | epoch UTC |
| step `timestamp` | `runtime_v2/api/agent_service_v2.py:523` (`time.gmtime()`) | **guest** | UTC |
| `exec_start`/`exec_end` | `qwen_train/f2_arm_worker.py:943,1070` (`time.time()`) | P1/worker | epoch UTC |
| `freeze_timestamp` | `runtime_v2/services/f2_freeze.py:111,237` (`time.time()`) | **host** | epoch UTC |
| timeout deadlines | `qwen_train/f2_execution_adapter.py:676,727` (`monotonic`) | host | monotonic |

**The 2025-01-01 validity floor.** `qwen_train/f2_protocol.py:175`
`_DELIVERY_EPOCH_FLOOR = 1735689600` (`2025-01-01T00:00:00Z`), enforced in
`parse_delivery_timestamp` at `:236` (ISO path) and `:262` (epoch path). An
earlier instant is rejected as implausible (fail closed); **no upper bound** is
imposed. This is a **validity/plausibility** check, not an ordering check: a
guest clock years in the past fails immediately; a clock slightly or days ahead
passes the floor but is caught by the skew check below.

**Cross-clock comparisons (source search).** No pass/fail comparison between a
guest-originated and a host-originated timestamp exists in the F2 path; the
freeze/delivery/service timestamps are recorded provenance. Ordering is
`NOT ESTABLISHED` as a correctness dependency; the floor is the only hard gate,
and it is validity-only.

**Guest clock startup check (added).** `qwen_train/f2_clock_guard.py`
(`evaluate_clock`) compares the guest UTC clock against a host UTC reference.
**Preferred reference: the gateway's FRESH HTTP `Date` header** on the authorized
`GET /v1/models` call (`qwen_train/f2_guest_probe.ps1` reads `$resp.Headers["Date"]`;
`evaluate_clock_http_date`). This reuses the already-authorized 8099 path — no
KVP, no PowerShell Direct, no extra channel, fresh at probe time. The
input-bundle manifest `host_reference_utc` is retained as **provenance only** and
is **never** a runtime fallback: if the HTTP `Date` is absent the probe emits
`HOST_REFERENCE_MISSING`; if malformed, `HOST_REFERENCE_MALFORMED` — both fail the
clock check. Distinct error classes: `HOST_REFERENCE_MISSING` /
`HOST_REFERENCE_MALFORMED` / `GUEST_TIMESTAMP_MALFORMED` /
`GUEST_CLOCK_OUTSIDE_ALLOWED_WINDOW`.

**PowerShell 5.1 (guest target).** The guest is Windows 11, whose built-in shell
is Windows PowerShell 5.1 (`C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`;
verified host `5.1.26100.9549`). The probe and the clock-vector harness are
PS 5.1-parse-clean (no `if`-as-hashtable-value, no `Test-Connection -TargetName`,
no `$PSScriptRoot` in param defaults, no `Headers.ContainsKey`). The **same clock
vectors** (`qwen_train/f2_clock_vectors.json`) are executed by the PowerShell
harness (`qwen_train/f2_clock_vectors.ps1`) under 5.1 and agree with the Python
authority (14/14). Guest launch:
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\f2_guest_probe.ps1 -GatewayIp 10.72.0.1 -GatewayPort 8099 -InputDisk <drive> -OutputPath C:\f2_probe.json`
(exit 0 = probe completed; the JSON, not the exit code, carries readiness). `|skew| > tolerance` ⇒ `FAIL` with `GUEST_CLOCK_OUTSIDE_ALLOWED_WINDOW`,
and the probe does not continue to network validation. Default tolerance
**120 s** (`DEFAULT_CLOCK_TOLERANCE_S`): the guest boot clock is seeded from the
host RTC and free-run drift over one arm is far under this, while day/year-scale
errors are caught. `tests/test_f2_clock_guard.py` covers valid / too-old /
too-new / exact-boundary / one-second-outside / malformed / missing /
deterministic cases. The probe (`qwen_train/f2_guest_probe.ps1`) emits the
schema in `qwen_train/f2_probe_schema.py` (validated by
`tests/test_f2_probe_schema.py`).

**Time Synchronization decision.** Final F2 keeps Hyper-V Time Synchronization
**disabled**. A one-time **installation/bootstrap** clock set (during install,
before the isolation ACL is relied upon) is a separate phase, not final
experiment-time synchronization, and requires no contract change. Enabling
*permanent* Time Synchronization for the final experiment would change the
authorized isolation contract → `REQUIRES AUTHORIZATION`.

**Classification.** Guest-origin `delivery_timestamp` = `PROVEN` (source).
2025-01-01 floor = `PROVEN` (source). Skew check = `PROVEN IN CURRENT REVISION`.
No cross-clock ordering gate = `PROVEN` (source). Drift impact on correctness =
`NOT ESTABLISHED`.

## 12. Evidence summary

| Claim | Label |
|---|---|
| Repository-side suppression / guard / gateway implemented | `PROVEN IN CURRENT REVISION` |
| Focused isolation + gateway tests pass (27) | `PROVEN IN CURRENT REVISION` |
| Full F2 suite + agents smoke clean (1438 passed / 5 skipped) | `PROVEN IN CURRENT REVISION` (F2-IMPL-AUTH-027) |
| F2 VM shell exists (Gen 2, exactly 1 NIC, Internal switch, authorized 3-rule ACL baseline) | `PROVEN IN CURRENT REVISION` (2026-10-07 host audit) |
| Guest-side egress/socket proof and Q9 attestation | `NOT ESTABLISHED` (VM exists; post-fix guest retest not obtained) |
| Provisioner default invocation performs **no** mutation | `PROVEN IN CURRENT REVISION` (dry-run: 0 VMs / 1 switch before and after) |
| Provisioner ACL: explicit `-Stateful $true`, `-IdleSessionTimeout 1800`, `ANY` catch-all deny, weight order, one allow, one NIC | `CODE PROOF` (`tests/test_f2_vm_provision_acl.py`) |
| Timeout ordering 900 < 960 < 1800 (client < gateway < Hyper-V idle) | `CODE PROOF` (`tests/test_f2_isolation_timeouts.py`) |
| Guest clock startup check + tolerance | `CODE PROOF` (`tests/test_f2_clock_guard.py`) |
| Input-bundle builder is plan-only by default; no VHDX/manifest on plan run | `PROVEN IN CURRENT REVISION` |
| Input-bundle builder full VHDX lifecycle (create/init/partition/format/populate/hash/dismount) | `SCRATCH-RUN PROOF` (2026-10-07 `$env:TEMP`) |
| Gateway HTTP `Date` header (fresh UTC clock reference) | `CODE PROOF` |
| Guest-side VHDX read-only (vs host `Mount-VHD -ReadOnly`) | `NOT ESTABLISHED` (needs guest write probe) |
| Effective request ceiling = model_router 300 s | `CODE PROOF` |
| Guest probe PS 5.1 parse-clean; vectors agree Python↔PS5.1 (14/14) | `CODE PROOF` + PS-RUN |
| IMAPI2 (`IMAPI2FS.MsftFileSystemImage`) available without ADK | `CODE PROOF` (host) |
| `overall_status` = PASS only if 0 required failed AND 0 required NOT_ESTABLISHED | `CODE PROOF` |
| DNS/IPv4/IPv6 semantics separate inventory from connectivity | `CODE PROOF` |
| Probe schema + probe script declared, no F2 execution invocation | `CODE PROOF` (`tests/test_f2_probe_schema.py`) |
| VM/ACL/IPv6/ICMP/guest-egress live enforcement | `NOT ESTABLISHED` (guest exists; no passing guest probe / Q9 attestation) |
| Guest `delivery_timestamp` origin (2-clock domains) | `PROVEN` (source) |
| 2025-01-01 delivery floor (`f2_protocol.py:175`) is validity-only | `PROVEN` (source) |
| No strict cross-clock ordering gate in the F2 path | `PROVEN` (source search) |
| Official guest install media obtained; guest installed | `PROVEN` (2026-10-07: VM exists, guest OOBE completed) |
| Host listener inventory / F2 guest reachability | `NOT ESTABLISHED` (ACL `FailedSecurityPolicy` drop observed in the failed diagnostic; cause unproven, no passing retest) |
| Model host-side; no GPU passthrough | `SUPPORTED` (design + host hardware) |

## 13. Limitations and human prerequisites

1. **DONE (2026-10-07).** Official Microsoft Windows 11 Enterprise 25H2 x64
   Evaluation ISO obtained (no repack / torrent / random VHDX); SHA-256 recorded
   at install time (see `WORK_LOG.md`).
2. **DONE.** ISO SHA-256 recorded and compared at install time
   (`WORK_LOG.md`).
3. **DONE.** `qwen_train/f2_vm_provision.ps1 -Execute -IsoPath <iso>` was run;
   Windows was installed in VMConnect **with the single NIC attached only to the
   isolated F2 Internal switch** (never Default/External/NAT, never a second
   adapter) and OOBE completed with a local account. The guest-side network /
   egress properties remain `NOT ESTABLISHED` (no passing guest probe, no Q9).
4. Guest static IP `10.72.0.2/24`, **no default gateway, no DNS** —
   **GUEST-REPORTED**, not host-verified. ISO detachment (the guest is currently
   `Off`) is `REQUIRES AUTHORIZATION` and was not performed by the audit.
5. Build the **input ISO** (design only; not built here) containing only
   immutable artifacts — Python 3.14 installer, offline wheels, the authorized
   repo snapshot, the runner/bootstrap, and immutable F2 inputs — each recorded
   as `filename, size, SHA-256, source, verification method` and hash-verified
   by the guest before use. `oscdimg` (Microsoft ADK) is the intended tool **if
   already installed**; no authoring tool is downloaded here.
6. For **egress**, write authorized evidence to a dedicated small **output
   VHDX**; attach/read it on the host **only after guest shutdown**; treat all
   recovered content as `UNTRUSTED`; record `filename, size, SHA-256, source VM
   identity, collection time`; never execute recovered files; cap size; detach.
   A second network port is **rejected** unless a future authorization proves it
   necessary.
7. If and only if read-only diagnosis proves Avast blocks the one authorized
   flow, Robert decides on the single narrow allow (`10.72.0.2 -> 10.72.0.1
   TCP 8099`). No agent may change Avast or Windows Firewall. **Historical note
   (2026-10-07 failed diagnostic):** read-only WFP inspection produced no
   matching net-event and no Avast rule was statically shown to match this flow,
   so Avast was **not** proven responsible; the enforcement component implicated
   by the vSwitch `FailedSecurityPolicy` counters was the **Hyper-V extended
   ACL**. No repair was proven (the post-fix guest retest was not obtained).

The evaluation OS has a **finite evaluation period**; the guest is intentionally
offline after installation. Items 5–6 are `REQUIRES AUTHORIZATION` before
implementation.

**Offline OOBE.** OOBE was **completed by the operator** on `F2-Isolation-VM`
with a local account (guest-reported, 2026-10-07), so installation did not
require weakening the network boundary. Whether the install ran fully offline is
`NOT ESTABLISHED` (not recorded). The standing rule holds: never add a temporary
adapter/switch/NAT or weaken the ACL to finish OOBE.

**PROPOSAL ONLY (not implemented, not authorized): golden-VHDX staging.** A
separate throwaway staging VM (NOT the F2 VM) could install Windows and complete
OOBE, apply updates only if separately authorized, remove all test data, verify
the resulting disk hash, verify no F2 data/secrets exist, shut down cleanly, and
transfer the resulting VHDX into the isolated F2 environment. It requires Rob to
authorize: the staging VM/switch, any install-time networking, the media, and the
transfer — then re-verify the one-NIC/ACL contract on the F2 VM.

## 14. Final verdict

`BLOCKED - REQUIRED HUMAN/EXTERNAL PREREQUISITE` — the VM shell now **exists**
(Generation 2; one NIC on the Internal `F2-Internal-Switch`; authorized
three-rule extended-ACL baseline) and the Windows guest is installed, **but F2
may not be run**: the guest→host gateway path was never proven (no post-fix
guest retest, **no Q9 attestation**), the VM topology is only **RECOMMENDED and
not authorized for F2 execution** (F2-IMPL-AUTH-025), and the readiness gate
(`python -m qwen_train.f2_preflight`) is **BLOCKED** by the privileged host
egress-enforcement step plus required evidence/authorization items
(`docs/EXPERIMENT_J_F2_OPERATOR_HANDOFF.md` §4). Repository-side isolation is
complete and tested. The experiment is **not** authorized to run.
