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

## 5. Network policy (proposed; provisioner-encoded)

- **Default deny**, both directions, all protocols (TCP/UDP/ICMP/other), IPv4
  and IPv6.
- **Single allow:** `Outbound, Allow, LocalIPAddress=10.72.0.2,
  RemoteIPAddress=10.72.0.1, RemotePort=8099, Protocol=TCP, Weight=100`,
  **stateful** via `-IdleSessionTimeout`.
- Allow weight (`100`) **outranks** the catch-all deny weight (`1`).
- Catch-all deny rules: `Outbound Deny weight 1` and `Inbound Deny weight 1`
  (no address/protocol/port ⇒ match all).
- No `0.0.0.0/0` allow. No second allow rule. No DHCP, no NAT, no default
  gateway, no DNS.

**Evidence classification:** the policy is **CODE PROOF** — it is encoded in
`qwen_train/f2_vm_provision.ps1` and is syntax-validated. It is
`NOT ESTABLISHED` as **VM NETWORK PROOF**: no VM exists, so no stored-ACL query,
IPv6 test, ICMP test, or guest socket probe has been performed. The provisioner
prints the stored ACL table (including `IdleSessionTimeout`) at apply time; that
print is the required proof step and has not run.

> Hyper-V note (must be confirmed at apply time, not assumed): extended ACLs
> are **stateless unless** `-IdleSessionTimeout` is supplied; the provisioner
> supplies it for the allow rule. Whether the no-argument catch-all deny covers
> IPv6 in practice must be confirmed from the stored ACLs and a live guest probe.

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

**Chosen direction:** Option A for input, Option B for output, with SHA-256
provenance on every artifact and an `UNTRUSTED OUTPUT` classification on
egress. `REQUIRES AUTHORIZATION` before implementation (see F2-IMPL-AUTH-027).

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

## 11. Secure Boot / vTPM

Secure Boot On and vTPM with a local key protector are set by the provisioner
for the Generation-2 VM. Shielded VM / HGS is **not** used (no requirement).
Intel GPU passthrough is **not** attempted: the model stays on the host by
design.

## 12. Evidence summary

| Claim | Label |
|---|---|
| Repository-side suppression / guard / gateway implemented | `PROVEN IN CURRENT REVISION` |
| Focused isolation + gateway tests pass (27) | `PROVEN IN CURRENT REVISION` |
| Full F2 suite + agents smoke clean (1438 passed / 5 skipped) | `PROVEN IN CURRENT REVISION` (F2-IMPL-AUTH-027) |
| No VM exists; no official guest media present | `PROVEN` (2026-10-07 host probe) |
| Provisioner encodes internal-only switch, one NIC, stateful allow + default deny | `CODE PROOF` |
| VM/ACL/IPv6/ICMP/guest-egress enforcement | `NOT ESTABLISHED` (no guest) |
| Model host-side; no passthrough | `SUPPORTED` (design + host hardware) |

## 13. Limitations and human prerequisites

1. Obtain the **official** Microsoft Windows 11 Enterprise evaluation ISO.
2. Verify its SHA-256 against Microsoft's published hash and record
   filename/size/SHA-256/published-hash/comparison.
3. Provision with `qwen_train/f2_vm_provision.ps1 -Execute -IsoPath <iso>`
   (after authorization); install Windows in VMConnect.
4. Configure the guest static IP `10.72.0.2/24`, **no default gateway, no DNS**;
   detach the ISO; boot offline.
5. If and only if read-only diagnosis proves Avast blocks the one authorized
   flow, Robert decides on the single narrow allow (`10.72.0.2 -> 10.72.0.1
   TCP 8099`). No agent may change Avast or Windows Firewall.

The evaluation OS has a **finite evaluation period**; the guest is intentionally
offline after installation.

## 14. Final verdict

`BLOCKED - EXTERNAL PREREQUISITE` — the official Windows guest install media is
absent, so the dedicated Internal-switch F2 VM cannot be created and no
VM/ACL/guest-egress property can be proven. Repository-side isolation is
complete and tested. The experiment is **not** authorized to run.
