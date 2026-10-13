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

## Documented evidence limitation (not worked around)

The event exists in the provider manifest, but the exact payload field names — and
whether the emitted event carries the full 5-tuple — are **NOT ESTABLISHED** without a
capture. The implementation therefore **requires** those fields and fails closed when
they are absent; it does not synthesise or infer them. Confirming the payload requires a
separately-authorized capture.

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
