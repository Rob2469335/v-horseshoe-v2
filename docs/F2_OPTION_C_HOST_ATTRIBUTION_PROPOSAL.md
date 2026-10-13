# F2 Option C — host-drop attribution for Q9 (PROPOSED, NOT ACTIVE)

**Status:** proposed, versioned (`option-c/1`), **off by default**, and **NOT active**.
Activating it for any experiment requires a separate, explicit owner decision. This
document is **not** an Experiment J authority document; the frozen design remains
`docs/EXPERIMENT_J*.md`.

## Why

The frozen Q9 contract accepts only an **active refusal** (`ConnectionRefusedError` /
`WSAEACCES 10013`) as `denied`. The F2 `vm` boundary is a Hyper-V **virtual-switch
extended port ACL**, which drops packets **silently** — so the guest observes a
timeout, the corrected classifier records `unknown`, and the dimension is unproven.
Q9 is therefore **NOT ESTABLISHED** on this boundary as the contract currently stands.

Option C adds a gated, versioned acceptance rule: an otherwise `unknown` denied-required
dimension may be accepted **only** when an **ACL-specific** host drop event is reliably
bound to the probe.

## The accepted evidence

`Microsoft-Windows-Hyper-V-VmSwitch` manifest **event id 131**, channel
`Microsoft-Windows-Hyper-V-VmSwitch-Diagnostic-Traffic`:

> "A packet was dropped on port %2 (Friendly Name: %4) on switch %6 (Friendly Name: %8)
> because the packet is filtered by %9 **extended port ACL entry** with weight %10."

A `HostDropRecord` must carry: the provider, event id `131`, channel, VM and switch
(`F2-Internal-Switch`), component (`vmswitch.sys`), direction (egress), protocol and
the literal destination (`ip_dst:port_dst`, matching the probe target), the extended
port ACL **entry** and **weight**, a 64-hex capture-artifact SHA-256, a capture window
that contains both the probe observation and the record's probe timestamp, freshness
within `DEFAULT_MAX_CAPTURE_AGE_SECONDS`, and the arm/rollout binding.

## What is rejected (fail closed)

Generic `Failed Security Policy` drops (the vSwitch applies many security policies, so
the reason alone does not identify the extended ACL); local `WSAEACCES` refusals without
host attribution; timeouts without qualifying host evidence; wrong provider / event /
component / switch; ingress direction; flow or protocol mismatch; a window that does not
contain the probe; stale or future captures; run-binding mismatch; multiple conflicting
qualifying records (ambiguous); missing fields; malformed records; and any record that
would override an observed `permitted` result. If no record qualifies, the dimension
stays `unproven` and Q9 stays `NOT ESTABLISHED`.

## Observed event-131 payload contract (read-only provider metadata, 2026-10-12)

Extracted from the provider manifest template via `Get-WinEvent -ListProvider` (no
capture). Event 131, channel `Microsoft-Windows-Hyper-V-VmSwitch-Diagnostic-Traffic`
(currently **disabled**), keywords `ut:Dropped` + `VmsDataPath`, level Warning. Data
fields:

`PortName`, `PortFName`, `SwitchName`, `SwitchFName`, `Direction` (UInt32), `Weight`
(UInt32) — each string preceded by its `*Len` length field.

- **PROVEN present:** port name + friendly name, switch name + friendly name, direction, weight.
- **PROVEN absent:** source/destination IP, source/destination port, protocol, and any
  packet/flow/correlation identifier. (Compare the Port-ACL event **36**, whose template
  *does* carry an `AclName` field — event 131 has none.)
- **NOT ESTABLISHED:** an explicit "extended port ACL entry" field. The message reads
  "%9 extended port ACL entry with weight %10", but the template exposes only `Direction`
  and `Weight`; the ACL is identifiable (at most) by `Weight`. On the live F2 adapter the
  default-deny outbound rule has **Weight 1** and the gateway allow **Weight 100**.

**Consequence — a correlation gap, not just a trust gap.** Event 131 cannot bind a drop
to the exact probe **flow** (no 5-tuple), and carries **no ACL-name/entry field**. The
`host_drop_attribution` gate as implemented requires an `acl_entry` and a flow match
(`ip_dst:port_dst == probe.target`); a real event 131 supplies **neither**, so the gate is
**inert (fails closed)** and cannot qualify a real drop. Flow correlation would have to
rest on `(switch, port, direction, weight, tight window)` only — a **rule change requiring
owner authorization** — and even then event 131 alone cannot identify the destination.

Confirming whether a live emitted event carries more than this template, and whether the
`%9` ACL entry resolves at all, requires a capture. Generating event 131 in turn requires a
packet dropped by the extended ACL on the F2 port — i.e. an authorized guest probe under an
ETW session; that probe is **not** authorized here.

## Evidence limitation (trust, separately)

Even if the payload carried the needed fields, a digest alone is not authentication: the
current gate validates record **shape**, not **origin** (no artifact bytes are parsed), so a
caller can construct an accepted record. A trusted-producer boundary or an owner-held
HMAC/signature trust anchor is required and is currently undefined.

## Separation from the frozen contract

- `qwen_train/f2_host_attribution.py` is a **new, separate module**.
- `qwen_train/f2_isolation.py` and `qwen_train/f2_readiness.py` are **unchanged**.
- The default (`acceptance=OPTION_C_BASELINE`) ignores all records and returns the
  frozen verdict exactly; only an explicit `acceptance=OPTION_C_ACCEPTANCE_VERSION`
  activates the rule.
- The readiness gate still requires the existing enforcement-identity binding; Option C
  does not relax it.

## Activation checklist (owner, separate authorization)

1. Confirm the payload contract of event 131 from an authorized capture.
2. Approve the acceptance-rule change (`option-c/1`) as a versioned contract amendment.
3. Wire `apply_host_attribution(..., acceptance=OPTION_C_ACCEPTANCE_VERSION)` into the
   readiness path (a deliberate, reviewed change) — not before.
4. Keep the baseline available as the default and as the rollback.

Files: `qwen_train/f2_host_attribution.py`, `tests/test_f2_host_attribution.py`.
