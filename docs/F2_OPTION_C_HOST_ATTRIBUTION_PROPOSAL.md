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
dimension may be accepted **only** when host evidence is reliably bound to the probe —
and only after an owner authorizes both the correlation rule and the producer trust
anchor. Until then the gate is inert and fails closed.

## Evidence sources

**Source A — Event 131** (`Microsoft-Windows-Hyper-V-VmSwitch`, channel
`…-Diagnostic-Traffic`, currently **disabled**). Read-only provider-manifest template:

> "A packet was dropped on port %2 (Friendly Name: %4) on switch %6 (Friendly Name: %8)
> because the packet is filtered by %9 **extended port ACL entry** with weight %10."

Data fields actually present: `PortName`, `PortFName`, `SwitchName`, `SwitchFName`,
`Direction`, `Weight` (each string preceded by its `*Len`).

- **PROVEN present:** port + friendly name, switch + friendly name, direction, weight.
- **PROVEN absent:** source/destination IP, ports, protocol, and any packet/flow/
  correlation id. (The Port-ACL event **36** carries an `AclName`; event 131 has none.)
- **NOT ESTABLISHED:** an explicit ACL-entry field. The ACL is identifiable (at most) by
  `Weight`. On the live F2 adapter the default-deny outbound rule is **Weight 1** and the
  gateway allow **Weight 100** — a configuration-dependent proxy, not an intrinsic id.

**Source B — flow-carrying packet-drop record** (pktmon-style): 5-tuple, protocol,
component identity, a generic `drop_reason`, timestamp, packet/flow id where available.
Its security-policy reason is **generic** and does **not** by itself identify the ACL.

**Consequence:** Event 131 alone cannot identify the destination flow. Only a *second*,
flow-carrying source can, and it does not identify the ACL. Correlation of the two is
**not causation** and must not be described as proof that the intended ACL caused a
specific drop.

## Trusted ingestion pipeline (implemented)

`qwen_train/f2_evidence_ingestion.py` defines the trust boundary between raw bytes and an
accepted attribution. Trusted types are **sealed** (raise `PermissionError` unless built
by the pipeline), and artifact digests are **computed from the bytes**, never asserted:

    OriginalArtifact  (raw bytes; digest computed)
      -> ParsedEvent    (only via parse_artifact; strict versioned schema, fail-closed)
      -> Observation    (validated, bound to the artifact digest)
      -> CorrelationCandidate (only via correlate)
      -> AcceptedAttribution  (only via accept_attribution)

Supported schemas: `vmswitch-acl-drop/1`, `pktmon-drop/1`. Parsing rejects malformed,
truncated, unsupported, empty, or field-incomplete artifacts. A caller cannot construct
a sealed type, and cannot supply a self-computed digest.

## Source authentication (trust policy)

`accept_attribution` takes a `TrustPolicy` and authenticates the **actual artifacts**
(both the ACL and flow sources) before accepting anything; trust is never read from a
caller-supplied verdict. Every contributing artifact must authenticate as the **same,
identified producer** — a mismatched producer, or an authenticated artifact with no
producer identity, is rejected. The default is `NullTrustPolicy`, which authenticates
**nothing**, so without an owner anchor every accept attempt returns `UNTRUSTED`.

- A digest proves bytes match a digest. It does **not** prove who produced them.
- A self-signed artifact or a caller-supplied key is **not** a trusted producer.
- No keys are created or provisioned here. The verifier interface is narrow so the owner
  can later provision an HMAC/signature anchor without redesigning the parser.

`What is authenticated`: nothing, by default. Only a policy the owner supplies can
authenticate a producer. `What is not`: artefact origin truthfulness, even when a policy
authenticates — a dishonest producer with a valid key can still lie.

## Two-source correlation and status model

`correlate(...)` matches Source A and Source B on: run identity, F2 switch, VM, direction
(egress), the probe **flow tuple** and protocol (from Source B), the ACL **weight** (from
Source A), and a bounded time window. Statuses, in order:

`UNTRUSTED` → `MALFORMED`/`UNSUPPORTED` → `NO_MATCH` → `AMBIGUOUS` →
`CORRELATED_CANDIDATE` → `ACCEPTED_ATTRIBUTION`.

`CORRELATED_CANDIDATE` means both sources agree on boundary/direction/time; it does **not**
prove the same physical packet caused both, nor that the ACL did. `AMBIGUOUS` (multiple
matching records) is never resolved by an arbitrary pick. The transition to
`ACCEPTED_ATTRIBUTION` additionally requires the authorized rule **and** an authenticated
producer.

## What is rejected (fail closed)

Fabricated in-memory records and direct construction of sealed types; artifacts whose
digest does not match the parsed record; malformed/unsupported/incomplete schemas; missing
source metadata; a missing or invalid trust anchor; generic `Failed Security Policy` used
as ACL identity; wrong provider/event/component/switch/VM/direction; flow or protocol
mismatch; wrong ACL weight; out-of-window or stale evidence; cross-run/replayed evidence;
ambiguous or conflicting matches; and anything that would override an observed `permitted`
result. If nothing qualifies, the dimension stays `unproven` and Q9 stays `NOT ESTABLISHED`.

## Separation from the frozen contract

- `qwen_train/f2_evidence_ingestion.py` (new) and `qwen_train/f2_host_attribution.py`
  (gate) are separate modules; `qwen_train/f2_isolation.py` and
  `qwen_train/f2_readiness.py` are **unchanged**.
- The gate accepts **only** sealed `AcceptedAttribution` tokens — it no longer accepts raw
  records or JSON, so the previous caller bypass is removed.
- The default (`acceptance=OPTION_C_BASELINE`, or any value other than the exact
  `option-c/1`) ignores the tokens and returns the frozen verdict exactly.
- The readiness gate still requires the existing enforcement-identity binding; Option C
  does not relax it.

## Activation checklist (owner, separate authorization)

1. Confirm, from an authorized capture, whether a live event 131 carries more than this
   template (and whether the ACL entry resolves at all).
2. Authorize the **correlation rule** (`option-c-correlation/1`): exact evidence threshold,
   ambiguity handling, and the scientific meaning of "attributed".
3. Authorize the **producer trust anchor** (who produces the artifacts; HMAC/signature and
   custody). Provisioning remains the owner's.
4. Wire `apply_host_attribution(..., acceptance=OPTION_C_ACCEPTANCE_VERSION)` into the
   readiness path (a deliberate, reviewed change) — not before.
5. Keep the baseline available as the default and as the rollback.

Files: `qwen_train/f2_evidence_ingestion.py`, `qwen_train/f2_host_attribution.py`,
`tests/test_f2_evidence_ingestion.py`, `tests/test_f2_host_attribution.py`.

## Owner decisions — recorded direction A3 + B2 (status OPEN; no authorization implied)

The working direction is **A3** (two-source correlation → `CORRELATED_CANDIDATE` only) and
**B2** (owner-controlled HMAC-SHA-256). It is a recorded design direction, **not**
authorization. Package (all under `C:\Users\rober\F2_EVIDENCE\`):
`Q9_OPTION_C_OWNER_DECISION_RECORD.md`, `Q9_OPTION_C_B2_HMAC_TRUST_DESIGN.md`,
`Q9_OPTION_C_NEXT_STAGE_AUTHORIZATION_CHECKLIST.md`.

Four things are held distinct: **artifact integrity** (bytes match a digest) · **producer
authenticity** (a trusted identity produced them — needs a key) · **temporal association**
(same window/boundary) · **causal attribution** (this control caused this drop).

- **A3 (recorded).** Event 131 has no flow tuple and no intrinsic packet id, and a generic
  flow-drop reason does not establish that the extended port ACL caused the drop; matching
  timestamps, tuples, port names, or configured weights do **not** prove packet identity or
  causation. Two-source evidence supports `CORRELATED_CANDIDATE` **only**, and candidate
  status is **not** established Q9 attribution. Ambiguous, missing, unauthenticated,
  mismatched, or unsupported evidence fails closed.
- **B2 (recorded).** Owner-controlled HMAC-SHA-256 with explicit producer identity, byte-exact
  canonical binding (artifact digest + experiment/arm/rollout/run ids), replay/duplicate
  rejection, rotation/revocation/compromise handling, and fail-closed verification. B2
  authenticates the **producer**, not its truthfulness or the science. **No key is created
  here.**

Gates **A–E** (correlation semantics · producer identity + trust policy · readiness-path
implementation · bounded live validation · evidence review) are **all open** and independent;
this document grants none. Until A and B are authorized and the anchor is provisioned, the
rule stays inactive and the frozen baseline remains the default. **Q9 remains NOT
ESTABLISHED.**
