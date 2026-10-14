# F2 / S8 — IPv6 Gate Decision Record (DRAFT — AWAITING OWNER DECISION)

**Status: DRAFT. No decision has been recorded. No waiver, verifier change, or
authorization is granted by this document.**

**Prepared:** 2026-10-14 · **Prepared by:** autonomous agent, within the
SOTA-governance closure assignment · **Decision-maker:** the **Operator** only
(the authorization register records this class of grant as *operator-granted* /
*operator-authorized*, e.g. `F2-IMPL-AUTH-013`, `-018`, `-028`).

This record isolates **one** unresolved policy question and offers three
mutually exclusive paths. It exists so the owner can answer precisely instead of
re-deriving the conflict. Selecting a path and signing it is an owner action.

---

## 1. The single unresolved question

> **Does the existing authority permit the five-task S8 pilot to execute while
> `verify_isolation_attestation` returns `satisfied=false` because the `ipv6`
> dimension is `unavailable`?**

Everything else on the isolation gate is settled. This is the only item that
requires an owner decision.

---

## 2. Conflicting statements, quoted exactly

### 2.1 Authority — what it says about the IPv6 *dimension*

`docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md:1046`:

> *Four outcomes, not two.* `denied` is recorded only when a probe was attempted
> and refused. Name-resolution failure is recorded `unknown`, never `denied`.
> **A dimension with no global IPv6 is `unavailable` with a reason — vacuously
> satisfied, but visibly not proven, and never laundered into `denied`.**

### 2.2 Authority — the §NETWORK acceptance list

Same file, `:658`:

> ACCEPTANCE: from inside the arm, `curl https://example.com` MUST fail; a raw
> socket to `1.1.1.1:53` MUST fail; a raw UDP packet MUST fail; **an IPv6 egress
> attempt MUST fail**; a proxy env var MUST NOT create a path; loopback to the
> model port MUST succeed; the host-side control MUST succeed.

### 2.3 The verifier — what it requires for overall satisfaction

`qwen_train/f2_isolation.py`, `DENIED_REQUIRED` includes `"ipv6"`;
`OUTCOME_UNAVAILABLE` is not a satisfying outcome, so `ipv6` lands in
`unproven_dimensions`, and `satisfied = not unproven and not failed`.

Pinned by two frozen tests in `tests/test_f2_isolation.py`:

> `test_ipv6_unavailable_is_unproven` — `assert not v.satisfied`;
> `assert "ipv6" in v.unproven_dimensions`

### 2.4 The §6 gate

`docs/EXPERIMENT_J_F2_S8_OFFLINE_EVALUATION_ISOLATION_PLAN.md` §7:
"verdict `satisfied=true` from `verify_isolation_attestation`. Until that verdict
is independently recomputed, isolation is `NOT ESTABLISHED`."

### 2.5 Owner ruling already given

Same file, §10a (owner, 2026-10-13): hold until the discrepancy is formally
resolved; the verifier must remain unchanged; no F2 task executes while the §6
gate is unsatisfied.

---

## 3. Why these are two questions, not one

| | Statement | Answers |
|---|---|---|
| **Q1** | "vacuously satisfied, but visibly not proven" | what the authority says about the **IPv6 dimension** |
| **Q2** | `satisfied = not unproven and not failed` | what the verifier requires **overall** |

They are **not in contradiction**. The authority's own sentence already contains
both halves: the dimension is *vacuous* (no risk) **and** *visibly not proven*.
The verifier implements the second half. So 2.1 does not, by itself, override
2.3.

What is **missing** is a fourth statement: text granting permission to execute
while the overall verdict is false.

**Search result — `NOT ESTABLISHED`.** A search of `docs/*.md` for permission to
proceed despite an unsatisfied verdict returned only this record and §10a. No
existing authority grants it.

---

## 4. Current runtime evidence (independently re-verified 2026-10-14)

| Fact | Value | Classification |
|---|---|---|
| Live outbound ACL | 12 rules, `w=350 Outbound Deny 0.0.0.0/0` present | `PROVEN` |
| Rule count before/after the change | 11 → 12, **1 added, 0 removed, 0 modified** | `PROVEN` |
| NAT / switch / routes / firewall / integration services | unchanged field-by-field | `PROVEN` |
| Mandatory loopback control (`127.0.0.1:1`, real listener) | `permitted` | `PROVEN` |
| `http` / `https` / `tcp` / `udp` | `enforcement_backed_dimensions` | `PROVEN` (config + capture) |
| `proxy` / `alternate_interface` | `policy_declared_dimensions` | `SUPPORTED` |
| Guest IPv6 | link-local `fe80::9e4d:462d:669a:212c/64` only; **no global IPv6, no `::/0` route** | `PROVEN` |
| Host IPv6 | no global IPv6 address | `PROVEN` |
| **IPv6 denial** | **NOT ESTABLISHED** — no path exists, but denial was never observed | `NOT ESTABLISHED` |
| Overall verifier result | **`satisfied=false`**, `unproven = ["ipv6"]`, `failed = []` | `PROVEN` |
| §NETWORK acceptance (7 of 7) | all met | `PROVEN` |

**Evidence artifacts (do not modify while pending):**

| Artifact | SHA-256 |
|---|---|
| `…\isolation_run_2026-10-13\attestation_in_guest_FINAL.json` | `1E6E3E9133F0B1DC6354BCDD8A43B8E82430B77F11A3E8AEC57473F05C8B7CC0` |
| `…\isolation_run_2026-10-13\enforcement_capture_recheck.json` | `E32654CADFB8D56D2A001D2F07A1C430147E18262CD603A8B67AAFC5A1A16484` |
| `…\isolation_run_2026-10-13\ISOLATION_FINAL_VERDICT.json` | `6D877CA71B92B430784224D4BD45D8E153014B20EAFBEF1695B08933C1B1ADAE` |
| `…\offline_iso_2026-10-13\acl_before_rules.json` | `B3188AB2B22B027C5366BBECC1E19957BF89EFC9ED5724BE3FEB0C8D519C4A4E` |
| `…\offline_iso_2026-10-13\acl_after_rules.json` | `3A3DC630A94D428D2AC8F0BEFF2C439C4F3F229C208C9EDDD071C5340AF674AA` |
| Capture digest claimed by the attestation | `943730170bbc5de97bd68d9793026b7fd3eb13d95a7566b731a1637122d90677` |

The claim matches the capture the host holds, so the enforcement binding is
intact. No dimension `failed`: nothing was observed reachable.

### 4a. Re-verification (2026-10-14)

Every fact in §4 was re-derived from the live host and the preserved artifacts
the following day, not recalled:

| Check | Result |
|---|---|
| Live ACL read-back | 12 rules; `w=350 Outbound Deny 0.0.0.0/0` present, effective order unchanged |
| VM identity / switch / NAT | `c5d294f5-…-ddc70dd94fed`, `F2-S8-NAT` (Internal), `10.73.0.0/24`; both VMs `Off` |
| Attestation claim vs live capture digest | **match** (`94373017…`) |
| Verifier verdict | **unchanged**: `satisfied=false`, `unproven=["ipv6"]`, `failed=[]` |
| Host global IPv6 | still not observed |
| `w=350` rule | preserved, not weakened, not removed |

The verdict is therefore as fresh as this record, and nothing in the intervening
day moved it. The decision below is required on current evidence, not on
stale evidence.

---

## 5. The three paths

### Path A — Existing authority permits proceeding

**Condition to satisfy:** identify governing text that explicitly permits
execution while `satisfied=false`.

**Current status: `NOT ESTABLISHED`.** The search in §3 found no such text, and
§2.1 addresses only the dimension, not the overall verdict.

**If the owner believes such text exists**, cite document + line here:

> _…owner to fill…_

**If Path A is chosen:** record the citation, confirm the other seven gates, and
the pilot may proceed with no waiver and no verifier change.

**Authorized decision-maker:** Operator. **Required record:** this section,
completed and dated.

---

### Path B — Narrowly scoped waiver

To be filled by the owner only if Path A is unavailable.

| Field | Value |
|---|---|
| Requirement waived | §6 gate `satisfied=true` **for the `ipv6` dimension only** |
| Scope | the five tasks of `F2_S8_EXECUTION_AND_PILOT_PACKAGE.md` §8; nothing else |
| Rationale | the guest has no IPv6 address and no IPv6 default route, so no IPv6 egress channel exists; §2.1 calls this *vacuously satisfied*; §2.2's "IPv6 egress attempt MUST fail" is met by the attempt failing |
| Risks | an unproven dimension is accepted as if proven; if IPv6 becomes reachable later, the waiver no longer describes reality |
| Compensating controls | `w=350` deny retained; `deny ANY` at `w=1` already covers IPv6; re-verify the guest has no global IPv6 immediately before execution; re-run the verifier and attach it |
| Expiry / review | single use, this pilot only; expires on any VM network change or on the confirmatory run, whichever is first |
| Approved by | _…operator…_ |
| Date | _…_ |

**Not implemented. Not exercised. Requires the owner's signature.**

---

### Path C — Formally authorized verifier change

**Exact semantic change under consideration:** treat
`OUTCOME_UNAVAILABLE` for a dimension whose address family is provably absent
(no global address **and** no default route) as satisfying that dimension,
while still never recording it as `denied`.

**Why it must not be done unilaterally:**
- It changes a **frozen, fail-closed** verifier contract.
- `tests/test_f2_isolation.py::test_ipv6_unavailable_is_unproven` pins
  `assert not v.satisfied`; changing the semantic changes that frozen test.
- The governing instruction prohibits "weakening, bypassing, silently
  reinterpreting, or removing a verifier requirement".
- The §NETWORK acceptance text and the verifier would then need to be re-checked
  for consistency — the very disagreement this record exists to settle.

**Required regression tests if ever authorized:** unavailable-IPv6 ⇒ satisfied
AND still not `denied`; a reachable IPv6 address still fails its dimension;
`unavailable` for a *present* address family still fails; the negative control
still cannot be satisfied by `unavailable`; round-trip/digest/schema unchanged.

**Authority needed:** Operator, explicitly, as a verifier change.

**Status: `REQUIRES AUTHORIZATION` — not implemented.**

---

## 6. Statements required by the closure assignment

- IPv6 **is unavailable** on the guest. `PROVEN`.
- The guest has **link-local IPv6 only, no global IPv6 and no default IPv6
  route**; the host also has no global IPv6. `PROVEN`.
- **IPv6 denial is `NOT ESTABLISHED`.** An absent path is not a denied path.
- The verifier's overall result is **`satisfied=false`**. `PROVEN`.
- **The five-task pilot remains held.** No experimental task has been executed.
- **No experimental observations have been admitted.** `trusted_store` holds 0
  files; no ledger exists; admitted = analyzable = completed = **0**.
- **No verifier change and no waiver has been approved by this work.**
- The **authorized owner must select and record the permitted path** (A, B, or C).

---

## 7. Owner selection

```
[ ] Path A — existing authority permits proceeding (cite document + line above)
[ ] Path B — narrowly scoped waiver (complete table in 5B, sign, date)
[ ] Path C — formally authorized verifier change (specify scope in 5C, sign, date)
[ ] None — remain on HOLD

Operator: ______________________________   Date: ______________
```

Until one path is selected, recorded, and its conditions met, the five-task
pilot stays on **HOLD**.
