# Experiment J / F2 - VM Isolation Boundary

**Scope:** the F2 host/guest isolation boundary only. This document is the
source of truth for the F2 VM topology, network policy, and isolation
validation. It does **not** define any F0 science and does **not** authorize
running the experiment.

**Status:** repository-side isolation is implemented and tested
(F2-IMPL-AUTH-027); the host/guest VM boundary is
`BLOCKED - EXTERNAL PREREQUISITE` (no official guest install media exists on
this host). See `docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md`
(F2-IMPL-AUTH-025/026/027) for the governing ledger entries.

Baseline revision when this document was written:
`488194d9b50263a237e8a1141e0ccc33aef1a990`.

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

| Property | Intended value | State |
|---|---|---|
| Switch | `F2-Internal-Switch`, type **Internal** | `BLOCKED - EXTERNAL PREREQUISITE` (not created) |
| VM | `F2-Isolation-VM`, **Generation 2** | not created |
| NIC count | **exactly 1** | not created (enforced fail-closed by the provisioner) |
| Host vEthernet | `10.72.0.1/24`, **no default gateway**, **no DNS** | not created |
| Guest | `10.72.0.2/24`, **no default gateway**, **no DNS** | not created |
| Secure Boot | On | not created |
| vTPM | Enabled, local key protector | not created |
| Integration services | Guest Service Interface / PowerShell Direct / Data Exchange / VSS / Time Sync **disabled**; Heartbeat + Guest Shutdown **enabled** | not created |

`Get-VM` returns **no VMs** and `Get-VMSwitch` returns only the OS-managed
`Default Switch` (Internal). `PROVEN` (2026-10-07).

## 5. Network policy (design; provisioner-encoded)

- **Default deny**, both directions, all protocols (TCP/UDP/ICMP/other), IPv4
  and IPv6.
- **Single allow** (parameters verbatim from the provisioner): `Outbound, Allow,
  LocalIPAddress=$GuestIp (10.72.0.2), RemoteIPAddress=$HostGatewayIp
  (10.72.0.1), RemotePort=$GatewayPort (8099), Protocol=TCP, Weight=100,
  Stateful=$true, IdleSessionTimeout=1800`.

**Timeout contract** (`qwen_train/f2_isolation_contract.py`): the Hyper-V idle
session timeout must exceed the longest upstream application timeout. Traced:
model client streaming ceiling **900 s** (`runtime_v2/services/_llm_client.py:617,633`;
other paths 120/300/600 s), F2 model gateway upstream timeout **960 s**
(`qwen_train/f2_model_gateway.py` `DEFAULT_TIMEOUT_S`), Hyper-V idle **1800 s**.
Strict ordering 900 < 960 < 1800 (~2× margin); `tests/test_f2_isolation_timeouts.py`
proves it. The guest NIC boundary is never the first timeout to kill a valid
stream.
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
`tests/test_f2_vm_provision_acl.py` (8 cases, no VM needed). It is
`NOT ESTABLISHED` as live **VM NETWORK PROOF**: no VM exists, so no stored-ACL
query, IPv6 test, ICMP test, or guest socket probe has run. At apply time the
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
because it needs no Microsoft ADK / `oscdimg` install and no download, has
inherent read-only attach semantics (the guest cannot write back), and carries an
easily verified SHA-256 manifest. Builder: `qwen_train/f2_input_bundle.ps1`
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
(`evaluate_clock`) compares the guest UTC clock against `host_reference_utc`
carried in the input-bundle manifest (the guest never contacts the host for the
time). `|skew| > tolerance` ⇒ `FAIL` with `GUEST_CLOCK_OUTSIDE_ALLOWED_WINDOW`,
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
| No VM, no F2 switch, no F2 adapter exists | `PROVEN IN CURRENT REVISION` (2026-10-07 host probe) |
| Provisioner default invocation performs **no** mutation | `PROVEN IN CURRENT REVISION` (dry-run: 0 VMs / 1 switch before and after) |
| Provisioner ACL: explicit `-Stateful $true`, `-IdleSessionTimeout 1800`, `ANY` catch-all deny, weight order, one allow, one NIC | `CODE PROOF` (`tests/test_f2_vm_provision_acl.py`) |
| Timeout ordering 900 < 960 < 1800 (client < gateway < Hyper-V idle) | `CODE PROOF` (`tests/test_f2_isolation_timeouts.py`) |
| Guest clock startup check + tolerance | `CODE PROOF` (`tests/test_f2_clock_guard.py`) |
| Input-bundle builder is plan-only by default; no VHDX/manifest on plan run | `PROVEN IN CURRENT REVISION` |
| Probe schema + probe script declared, no F2 execution invocation | `CODE PROOF` (`tests/test_f2_probe_schema.py`) |
| VM/ACL/IPv6/ICMP/guest-egress live enforcement | `NOT ESTABLISHED` (no guest) |
| Guest `delivery_timestamp` origin (2-clock domains) | `PROVEN` (source) |
| 2025-01-01 delivery floor (`f2_protocol.py:175`) is validity-only | `PROVEN` (source) |
| No strict cross-clock ordering gate in the F2 path | `PROVEN` (source search) |
| Official guest install media present | `BLOCKED — EXTERNAL PREREQUISITE` |
| Host listener inventory / future guest reachability | `INFERRED` (no F2 interface yet) |
| Model host-side; no GPU passthrough | `SUPPORTED` (design + host hardware) |

## 13. Limitations and human prerequisites

1. Obtain the **official** Microsoft Windows 11 Enterprise 25H2 x64 Evaluation
   ISO (no repack / torrent / random VHDX).
2. Verify its SHA-256 against Microsoft's published hash and record
   filename/size/SHA-256/published-hash/comparison.
3. Authorize and run `qwen_train/f2_vm_provision.ps1 -Execute -IsoPath <iso>`;
   install Windows in VMConnect **with the single NIC attached only to the
   isolated F2 Internal switch** (never Default/External/NAT, never a second
   adapter). `INFERRED — MUST BE VERIFIED DURING INSTALL`: the audit does not
   guarantee that the current Enterprise 25H2 evaluation build permits an
   offline/local-account install. Attempt the documented offline (OOBE) route if
   offered; **if the build cannot complete without Internet/account
   connectivity, STOP and report the exact screen/error** — do not weaken the
   network boundary.
4. Configure the guest static IP `10.72.0.2/24`, **no default gateway, no DNS**;
   detach the ISO; boot offline.
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
   TCP 8099`). No agent may change Avast or Windows Firewall.

The evaluation OS has a **finite evaluation period**; the guest is intentionally
offline after installation. Items 5–6 are `REQUIRES AUTHORIZATION` before
implementation.

## 14. Final verdict

`BLOCKED - EXTERNAL PREREQUISITE` — the official Windows guest install media is
absent, so the dedicated Internal-switch F2 VM cannot be created and no
VM/ACL/guest-egress property can be proven. Repository-side isolation is
complete and tested. The experiment is **not** authorized to run.
