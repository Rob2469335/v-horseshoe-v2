"""F2 clean-room network-isolation attestation: the non-privileged layers.

Why this module exists
----------------------
``qwen_train.f2_readiness`` consumes a ``no_egress`` mapping of seven
caller-asserted strings. That is a *report*, not evidence: nothing in the
repository observed a socket, so the strings cannot distinguish "egress was
denied" from "somebody typed the word denied". This module supplies the missing
observation and verification layers WITHOUT performing or requiring any
privileged host operation.

Scope boundary (deliberate)
---------------------------
This module **observes and verifies**. It does not create firewall rules, bind
interfaces, alter DNS, or require Administrator. Enforcing egress denial is a
privileged host control and remains outside it; what lives here is everything
that can be done unprivileged:

* run real connectivity probes and record what actually happened;
* record the evidence a reviewer needs to disbelieve the result (policy
  identity, interface inventory, DNS behaviour, positive controls);
* require a negative control, so an over-block that also breaks loopback cannot
  masquerade as correct isolation;
* bind the attestation to a specific arm/rollout/workspace;
* recompute a fail-closed verdict from the evidence rather than trusting it.

Honesty properties this module enforces
---------------------------------------
* An unobserved dimension is ``unknown``, never ``denied``.
* ``denied`` is only recorded when a probe was actually attempted and refused
  by the network stack; a routing failure (no route) or an ambiguous reset is
  ``unknown``, never ``denied``.
* A missing or failing negative control invalidates the whole attestation.
* DNS behaviour must be declared explicitly; it is never inferred.
* If IPv6 is not configured on the host, that dimension is ``unavailable`` with a
  reason -- it is not silently counted as ``denied``.

Enforcement-backed denial (added 2026-10-13)
--------------------------------------------
A packet filter that DROPs rather than REJECTs is a legitimate denial
mechanism, and the probe observer cannot tell a dropped SYN from a blackhole
address: both look like a timeout.  Until 2026-10-13 the contract therefore
demanded a ``WSAEACCES``/RST refusal that a Hyper-V VM extended ACL (which
silently drops in the vSwitch) can never produce, so an *correctly enforced*
ACL boundary could never be attested.  That was a defect in the procedure, not
in the enforcement.

The fix is deliberately NOT "a timeout is a denial".  Three things must all
hold before a timed-out denied-required dimension is satisfied:

1. the attestation claims a digest (``enforcement_capture_sha256``) of the
   enforcement artifact the observation was made under;
2. the HOST-side verifier is handed that capture independently and it hashes to
   the claimed digest -- a guest can never supply its own enforcement;
3. the capture's own rules *derive* the denial: the highest-weight matching
   outbound rule for the dimension's literal target must be an explicit
   ``Deny``.  A deny buried beneath a higher-weight allow does not count.

A dimension satisfied this way is reported in
``VerificationVerdict.enforcement_backed_dimensions`` -- never in
``satisfied_dimensions`` -- so an observer of the verdict can always tell an
observed refusal from an enforcement-attributed drop.  The mandatory
negative control (a real loopback listener) must still be observed ``permitted``,
which proves the probe harness can see a reachable path.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import platform
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "DIMENSIONS",
    "OUTCOME_DENIED",
    "OUTCOME_PERMITTED",
    "OUTCOME_UNKNOWN",
    "OUTCOME_UNAVAILABLE",
    "DENIED_REQUIRED",
    "PERMITTED_REQUIRED",
    "ENFORCEMENT_CAPTURE_SCHEMA",
    "IsolationAttestation",
    "ProbeResult",
    "VerificationVerdict",
    "default_egress_probe",
    "run_egress_probes",
    "build_attestation",
    "verify_isolation_attestation",
    "render_attestation",
    "parse_attestation",
    "attestation_digest",
    "canonical_enforcement_digest",
    "canonical_rules_digest",
    "parse_enforcement_capture",
    "effective_outbound_rule",
    "enforcement_denies",
]

#: Every dimension the F2 clean-room contract must speak to. ``denied_required``
#: dimensions must be observed-and-refused; ``permitted_required`` dimensions
#: must be observed-and-reachable, because a total blackout that also breaks
#: loopback is not isolation.
DIMENSIONS: tuple[str, ...] = (
    "http",
    "https",
    "tcp",
    "udp",
    "ipv6",
    "proxy",
    "loopback",
    "dns",
    "required_service",
    "alternate_interface",
)
DENIED_REQUIRED: frozenset[str] = frozenset(
    {"http", "https", "tcp", "udp", "ipv6", "proxy", "alternate_interface"}
)
PERMITTED_REQUIRED: frozenset[str] = frozenset({"loopback", "required_service"})

#: The ONLY dimensions a policy assertion may satisfy. Every other dimension is
#: observer-provable and must be measured; an assertion for it is not accepted.
_POLICY_ASSERTABLE_DIMENSIONS: frozenset[str] = frozenset(
    {"proxy", "alternate_interface"}
)

OUTCOME_DENIED = "denied"
OUTCOME_PERMITTED = "permitted"
OUTCOME_UNKNOWN = "unknown"
OUTCOME_UNAVAILABLE = "unavailable"

#: Never treat these as evidence that egress is blocked: they prove nothing about
#: the destination and are exactly the ambiguity a name-based probe creates.
_PROBE_TIMEOUT_SECONDS = 2.0

#: Windows socket error codes that represent a WFP/firewall REFUSAL -- the stack
#: refused to send the packet. These, and only these, are recorded as ``denied``.
_FIREWALL_REFUSAL_WINERRORS = frozenset({10013})  # WSAEACCES

#: Windows socket error codes that mean NO ROUTE was found. The packet never left
#: the stack and never reached any enforcement point, so these are NOT evidence
#: of firewall enforcement -- a missing default route produces exactly this.
_UNREACHABLE_WINERRORS = frozenset({10051, 10065})  # WSAENETUNREACH / WSAEHOSTUNREACH


@dataclass(frozen=True)
class ProbeResult:
    """One observed connectivity attempt.

    ``outcome`` is the *classified* result (``permitted`` / ``denied`` /
    ``unknown`` / ``unavailable``).  ``raw_outcome`` preserves what the probe
    actually saw before classification -- ``timeout``, ``silent``,
    ``unreachable``, ``icmp_unreachable``, ``dns_unresolved``, ``error`` --
    so the evidence can distinguish a silent packet drop from an unexecuted
    control, an absent route, and a name-resolution failure.  A verifier that
    looked only at ``outcome`` could not.
    """

    dimension: str
    target: str
    protocol: str
    outcome: str
    detail: str = ""
    local_address: str = ""
    elapsed_ms: int = 0
    pid: int = 0
    at: float = 0.0
    raw_outcome: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "target": self.target,
            "protocol": self.protocol,
            "outcome": self.outcome,
            "detail": self.detail,
            "local_address": self.local_address,
            "elapsed_ms": self.elapsed_ms,
            "pid": self.pid,
            "at": round(float(self.at), 3),
            "raw_outcome": self.raw_outcome,
        }


@dataclass(frozen=True)
class IsolationAttestation:
    """Signed-by-digest record of one clean-room isolation observation."""

    schema: str
    arm_id: str
    rollout_id: str
    workspace: str
    observer_pid: int
    policy_identity: str
    policy_sha256: str
    interface_inventory: tuple[str, ...]
    dns_behavior: str
    negative_control: str
    probes: tuple[ProbeResult, ...]
    required_services: tuple[str, ...] = ()
    policy_assertions: tuple[tuple[str, str], ...] = ()
    notes: str = ""
    # --- enforcement identity (added 2026-10-05) -------------------------
    # An attestation previously bound only arm/rollout/workspace, so a denial
    # was UNATTRIBUTABLE: nothing in the record said WHICH control produced it.
    # These fields make the control a first-class, digest-bound part of the
    # evidence. All default to empty, which ``assess_enforcement_identity``
    # treats as NOT ESTABLISHED -- never as "enforced".
    executing_user: str = ""
    interpreter_path: str = ""
    interpreter_sha256: str = ""
    spawn_image_inventory: tuple[str, ...] = ()
    enforcement_scope: str = ""
    # --- enforcement-backed denial (added 2026-10-13) ---------------------
    # A claim, not evidence: the SHA-256 of the HOST-captured enforcement
    # artifact (see ``ENFORCEMENT_CAPTURE_SCHEMA``) this observation was made
    # under.  Empty means no enforcement artifact was bound, and the verifier
    # then falls back to the strict observed-refusal contract.  The guest
    # cannot supply the capture itself -- only this digest -- so it can never
    # assert its own enforcement.
    enforcement_capture_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "arm_id": self.arm_id,
            "rollout_id": self.rollout_id,
            "workspace": self.workspace,
            "observer_pid": self.observer_pid,
            "policy_identity": self.policy_identity,
            "policy_sha256": self.policy_sha256,
            "interface_inventory": list(self.interface_inventory),
            "dns_behavior": self.dns_behavior,
            "negative_control": self.negative_control,
            "required_services": list(self.required_services),
            "executing_user": self.executing_user,
            "interpreter_path": self.interpreter_path,
            "interpreter_sha256": self.interpreter_sha256,
            "spawn_image_inventory": list(self.spawn_image_inventory),
            "enforcement_scope": self.enforcement_scope,
            "enforcement_capture_sha256": self.enforcement_capture_sha256,
            "policy_assertions": [
                {"dimension": d, "assertion": a, "source": "enforced_policy"}
                for d, a in self.policy_assertions
            ],
            "notes": self.notes,
            "probes": [p.to_dict() for p in self.probes],
        }


#: The schema string this module emits.  It is deliberately UNCHANGED by the
#: 2026-10-13 additions: every new field is optional with a default, so an
#: older artifact still parses (and an older parser still reads a newer
#: artifact, ignoring the fields it does not know).
SCHEMA = "f2_isolation_attestation_v1"

#: Schema of the host-captured enforcement artifact that can back a silent
#: drop.  Supplied by the HOST-side verifier, never by the probed guest.
ENFORCEMENT_CAPTURE_SCHEMA = "f2_enforcement_capture_v1"

#: Kinds of enforcement capture this repository knows how to *derive coverage
#: from*.  An unrecognised kind is rejected: coverage must be re-derived from
#: real rules, never trusted from a label.
SUPPORTED_ENFORCEMENT_KINDS = ("hyper-v-extended-acl",)


def default_egress_probe(protocol: str, host: str, port: int) -> tuple[str, str, str]:
    """Attempt one connection and report ``(outcome, detail, local_address)``.

    This is the ONLY place a real socket is opened. It is a plain observer: it
    creates no rule, requires no privilege, and changes nothing about the host.

    ``outcome`` is ``denied`` only when the attempt was refused by the network
    stack. A refusal that is clearly *name resolution* is reported as
    ``dns_unresolved`` rather than ``denied``, because an unresolvable name is not
    evidence that the destination was blocked.
    """
    start = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, port, type=_SOCK_TYPE[protocol])
    except socket.gaierror as exc:
        return "dns_unresolved", f"name resolution failed: {exc}", ""
    except Exception as exc:  # noqa: BLE001
        return "error", f"{type(exc).__name__}: {exc}", ""

    local = ""
    family = 0
    sock = None
    try:
        family, socktype, proto, _canon, sockaddr = infos[0]
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(_PROBE_TIMEOUT_SECONDS)
        sock.connect(sockaddr)
        try:
            local = sock.getsockname()[0]
        except OSError:
            local = ""
        outcome = OUTCOME_PERMITTED
        detail = f"connected to {host}:{port} via {local_address_of(family)}"
    except ConnectionRefusedError as exc:
        outcome = OUTCOME_DENIED
        detail = f"connection refused: {exc}"
    except socket.timeout:
        # A timeout (silent drop) is AMBIGUOUS: it is produced by a firewall
        # drop, an absent listener, a blackhole address, or a filtered path.
        # It is NOT a refusal and must never establish a denial dimension.
        outcome = "timeout"
        detail = "timed out (silently dropped or filtered; not a refusal)"
    except OSError as exc:
        # Only a WFP/firewall *refusal* counts as denied. A routing fault or a
        # reset does NOT prove the enforcement mechanism refused the packet:
        #   * WSAEACCES (10013) -- the stack refused to send: a block.
        #   * WSAENETUNREACH (10051) / WSAEHOSTUNREACH (10065) -- no route was
        #     found, so the packet never reached any enforcement point. This is
        #     the signature of a MISSING ROUTE, not of a firewall.
        #   * WSAECONNRESET (10054) and anything else -- ambiguous; a reset can
        #     come from any peer or middlebox and does not prove this control.
        # Unreachable/ambiguous outcomes are `unknown` (see `_classify`) and
        # cannot satisfy a denied-required dimension.
        winerror = getattr(exc, "winerror", None)
        if winerror in _FIREWALL_REFUSAL_WINERRORS:
            outcome = OUTCOME_DENIED
        elif winerror in _UNREACHABLE_WINERRORS:
            outcome = "unreachable"
        else:
            outcome = "error"
        detail = f"{type(exc).__name__} (winerror={winerror}): {exc}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    elapsed = int((time.perf_counter() - start) * 1000)
    del elapsed
    return outcome, detail, local


_SOCK_TYPE = {"http": socket.SOCK_STREAM, "https": socket.SOCK_STREAM,
              "tcp": socket.SOCK_STREAM, "dns": socket.SOCK_DGRAM}


def local_address_of(family: int) -> str:
    return "ipv6" if family == socket.AF_INET6 else "ipv4"


def default_udp_probe(host: str, port: int) -> tuple[str, str, str]:
    """Send ONE UDP datagram and report the observable result.

    Unlike a UDP ``connect()`` (which only associates a peer and sends nothing),
    this transmits a real datagram, so it can observe a local send error or an
    ICMP-port-unreachable. It is still honest about what it CANNOT show:

    * a successful ``sendto`` proves only that the datagram LEFT the stack --
      never that any peer received it;
    * a silent drop is indistinguishable from an absent listener, so silence is
      ``silent`` (``unknown``), never ``denied``;
    * an ICMP port-unreachable means the destination was REACHED and had no
      listener -- also not evidence of a firewall denial.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except socket.gaierror as exc:
        return "dns_unresolved", f"name resolution failed: {exc}", ""
    except Exception as exc:  # noqa: BLE001
        return "error", f"{type(exc).__name__}: {exc}", ""

    local = ""
    sock = None
    try:
        family, socktype, proto, _canon, sockaddr = infos[0]
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(_PROBE_TIMEOUT_SECONDS)
        sock.sendto(b"", sockaddr)  # a real datagram leaves the stack
        try:
            local = sock.getsockname()[0]
        except OSError:
            local = ""
        try:
            sock.recvfrom(1)
            outcome = OUTCOME_PERMITTED  # a peer actually answered
            detail = f"received a datagram from {host}:{port}"
        except socket.timeout:
            outcome = "silent"
            detail = "sent; no response (silent drop or absent listener)"
        except ConnectionResetError as exc:
            outcome = "icmp_unreachable"
            detail = f"ICMP port unreachable: {exc}"
    except ConnectionResetError as exc:
        outcome = "icmp_unreachable"
        detail = f"ICMP port unreachable: {exc}"
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        if winerror in _FIREWALL_REFUSAL_WINERRORS:
            outcome = OUTCOME_DENIED
        elif winerror in _UNREACHABLE_WINERRORS:
            outcome = "unreachable"
        else:
            outcome = "error"
        detail = f"{type(exc).__name__} (winerror={winerror}): {exc}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return outcome, detail, local


def _classify(dimension: str, raw: str) -> str:
    """Map a raw probe outcome to a schema outcome.

    Only ``permitted``, ``denied`` and ``unavailable`` pass through. Every
    ambiguous raw outcome -- ``dns_unresolved``, ``timeout``, ``silent``,
    ``icmp_unreachable``, ``unreachable``, ``error`` -- is ``unknown`` and can
    never satisfy a required-denial dimension.
    """
    if raw in (OUTCOME_PERMITTED, OUTCOME_DENIED, OUTCOME_UNAVAILABLE):
        return raw
    return OUTCOME_UNKNOWN


def is_sha256_hex(value: str) -> bool:
    """True iff ``value`` is a syntactically valid 64-character SHA-256 digest."""
    return len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def canonical_rules_digest(capture: Mapping[str, Any]) -> str:
    """SHA-256 over ONLY the enforcement RULES of a capture (no ``captured_at``,
    no ``subject``).

    Backward-compatible ADDITION: it changes nothing about
    :func:`canonical_policy_digest` / :func:`canonical_enforcement_digest`, which
    intentionally identify a *capture* (byte-identity, including
    ``captured_at``). It answers the DISTINCT question "are these two captures
    the SAME POLICY?" -- the same rule set -- which the capture digests cannot,
    because a re-capture of an unchanged ACL yields a different capture digest.
    It never replaces, and is never compared against, an attestation's
    ``policy_sha256``; ``captured_at`` is deliberately EXCLUDED so this digest
    can never satisfy a freshness requirement.
    """
    rules = capture.get("rules") if isinstance(capture, Mapping) else None
    payload = [r for r in rules if isinstance(r, Mapping)] if isinstance(rules, (list, tuple)) else []

    def _weight(r: Mapping[str, Any]) -> int:
        w = r.get("weight", 0)
        if isinstance(w, bool) or not isinstance(w, int):
            try:
                w = int(str(w).strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"malformed enforcement rule: weight {w!r} is not an integer"
                ) from exc
        return w

    def _key(r: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            str(r.get("direction", "")).lower(),
            str(r.get("action", "")).lower(),
            str(r.get("remote_ip", "")),
            str(r.get("protocol", "any")).lower(),
            _weight(r),
            bool(r.get("stateful", False)),
        )

    normalized = [list(k) for k in sorted(_key(r) for r in payload)]
    blob = json.dumps(normalized, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def canonical_policy_digest(capture: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 of a policy-capture payload (repository convention).

    This identifies a *configuration capture*. It does NOT, by itself, prove the
    configuration was enforced: a caller that recomputes it over the live host
    capture and compares it to an attestation's ``policy_sha256`` verifies the
    *identity* of a capture, never enforcement.
    """
    blob = json.dumps(capture, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def policy_digest_matches(
    att: IsolationAttestation | Mapping[str, Any],
    capture: Mapping[str, Any],
) -> bool:
    """Host-side check: does the attestation name THIS policy capture?

    Recomputes the canonical digest of ``capture`` and compares it to the
    attestation's ``policy_sha256``. Establishes byte-identity of a capture --
    NEVER that the policy was enforced, and never who produced the capture.
    """
    if isinstance(att, Mapping):
        att = parse_attestation(att)
    return bool(att.policy_sha256) and att.policy_sha256 == canonical_policy_digest(capture)


# --------------------------------------------------------------------------
# Enforcement capture: host-side artifact whose OWN rules derive a denial.
# --------------------------------------------------------------------------
def canonical_enforcement_digest(capture: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 of an enforcement capture, same convention as
    :func:`canonical_policy_digest`.

    Identifies a *capture*; says nothing about whether it was enforced.  The
    enforcement claim is only accepted when the verifier is handed the capture
    itself and re-derives coverage from its rules.
    """
    blob = json.dumps(capture, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def parse_enforcement_capture(capture: Mapping[str, Any] | bytes) -> dict[str, Any]:
    """Validate an enforcement capture.  Raises ``ValueError`` on anything
    that could not be used to *derive* coverage -- a label is never enough."""
    if isinstance(capture, (bytes, bytearray)):
        try:
            capture = json.loads(bytes(capture).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - fail closed
            raise ValueError(f"enforcement capture is not valid JSON: {exc}") from exc
    if not isinstance(capture, Mapping):
        raise ValueError("enforcement capture must be a JSON object")
    if capture.get("schema") != ENFORCEMENT_CAPTURE_SCHEMA:
        raise ValueError(
            f"enforcement capture schema {capture.get('schema')!r} != "
            f"{ENFORCEMENT_CAPTURE_SCHEMA!r}"
        )
    kind = str(capture.get("kind") or "")
    if kind not in SUPPORTED_ENFORCEMENT_KINDS:
        raise ValueError(
            f"enforcement kind {kind!r} is not one this repository can derive "
            f"coverage from: {SUPPORTED_ENFORCEMENT_KINDS}"
        )
    subject = capture.get("subject")
    if not isinstance(subject, Mapping):
        raise ValueError("enforcement capture is missing its subject")
    for field in ("vm_name", "vm_id", "adapter_mac"):
        if not str(subject.get(field) or "").strip():
            raise ValueError(f"enforcement capture subject.{field} is required")
    rules = capture.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("enforcement capture carries no rules")
    normalised: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            raise ValueError(f"enforcement rule {index} is not an object")
        try:
            weight = int(rule["weight"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"enforcement rule {index} has no integer weight") from exc
        direction = str(rule.get("direction") or "").strip().lower()
        action = str(rule.get("action") or "").strip().lower()
        remote = str(rule.get("remote_ip") or "").strip()
        if direction not in ("inbound", "outbound"):
            raise ValueError(f"enforcement rule {index} has direction {direction!r}")
        if action not in ("allow", "deny"):
            raise ValueError(f"enforcement rule {index} has action {action!r}")
        if not remote:
            raise ValueError(f"enforcement rule {index} has no remote_ip")
        normalised.append(
            {
                "direction": direction,
                "action": action,
                "remote_ip": remote,
                "protocol": str(rule.get("protocol") or "any").strip().lower(),
                "weight": weight,
                "stateful": bool(rule.get("stateful")),
            }
        )
    # The subject is carried through VERBATIM (beyond the three required
    # fields) so that informative extras -- adapter name, switch -- survive.
    # Coverage is derived from the normalised rules, never from the extras.
    return {
        "schema": ENFORCEMENT_CAPTURE_SCHEMA,
        "kind": kind,
        "subject": {k: str(v) for k, v in subject.items()},
        "captured_at": str(capture.get("captured_at") or ""),
        "rules": normalised,
    }


def _ip_in_spec(spec: str, address: "ipaddress._BaseAddress") -> bool:
    """Does a rule's remote-address spec cover ``address``?

    Understands ``ANY`` (every address), a host address, a CIDR subnet, and
    the ``0.0.0.0/0`` / ``::/0`` all-address forms.  An unparsable spec covers
    nothing -- an unreadable rule must never be counted as coverage.
    """
    text = str(spec or "").strip()
    if not text:
        return False
    if text.upper() == "ANY":
        return True
    try:
        if "/" in text:
            return address in ipaddress.ip_network(text, strict=False)
        return address == ipaddress.ip_address(text)
    except ValueError:
        return False


#: Raw probe outcomes that mean "the packet produced no observable answer".
#: These are the ONLY outcomes an enforcement-backed denial may replace, and
#: only when they are the dimension's sole outcome.
_AMBIGUOUS_RAW_OUTCOMES = frozenset({"timeout", "silent"})


def effective_outbound_rule(
    capture: Mapping[str, Any], address: str, protocol: str
) -> dict[str, Any] | None:
    """The single highest-weight outbound rule matching ``address``/``protocol``.

    Returns ``None`` when nothing matches or when two matching rules tie on
    weight -- an ambiguous ordering cannot establish what wins, so it must not
    be used to attribute a denial.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    proto = str(protocol or "tcp").strip().lower()
    matched = [
        rule
        for rule in capture["rules"]
        if rule["direction"] == "outbound"
        and _ip_in_spec(rule["remote_ip"], parsed)
        and rule["protocol"] in ("any", proto)
    ]
    if not matched:
        return None
    best = max(r["weight"] for r in matched)
    winners = [r for r in matched if r["weight"] == best]
    return winners[0] if len(winners) == 1 else None


def enforcement_denies(
    capture: Mapping[str, Any], address: str, protocol: str
) -> bool:
    """True iff the capture's EFFECTIVE rule for this target is an explicit
    ``Deny``.  A deny that a higher-weight allow outranks is not coverage."""
    rule = effective_outbound_rule(capture, address, protocol)
    return bool(rule and rule["action"] == "deny")


def _dimension_targets_addressed(results: Sequence[ProbeResult], dim: str) -> list[tuple[str, str]]:
    """Literal ``(address, protocol)`` pairs a dimension was probed with.

    A target that is not a literal address (a DNS name) cannot be matched
    against a rule, so it makes the dimension un-backable -- correctly, since
    name-dependent evidence is exactly what this contract refuses to trust.
    """
    pairs: list[tuple[str, str]] = []
    for probe in results:
        if probe.dimension != dim:
            continue
        target = str(probe.target)
        address = target.rsplit(":", 1)[0] if ":" in target else target
        address = address.strip("[]")
        try:
            ipaddress.ip_address(address)
        except ValueError:
            return []
        proto = "tcp" if str(probe.protocol).lower() in ("tcp", "tcp6", "http", "https") else "udp"
        pairs.append((address, proto))
    return pairs


#: Denied-required external categories, keyed by destination port. A target's
#: port decides which acceptance dimension its observation satisfies.
_EXTERNAL_DIM_BY_PORT = {80: "http", 443: "https"}

#: Default external destinations for the denied-required TCP dimensions.
#: LITERAL addresses only: this runner must not depend on DNS to decide whether
#: an outbound path is blocked, because an unresolvable name proves nothing
#: about egress (see ``default_egress_probe``). Callers override these with
#: ``--external HOST:PORT``; the destination port selects http/https/tcp.
DEFAULT_EXTERNAL_TARGETS: tuple[tuple[str, int], ...] = (
    ("93.184.216.34", 80),    # http
    ("93.184.216.34", 443),   # https
    ("93.184.216.34", 8080),  # tcp
)


def run_egress_probes(
    *,
    probe: Callable[[str, str, int], tuple[str, str, str]] = default_egress_probe,
    external_targets: Sequence[tuple[str, int]] = DEFAULT_EXTERNAL_TARGETS,
    udp_target: tuple[str, int] = ("8.8.8.8", 53),
    udp_probe: Callable[[str, int], tuple[str, str, str]] = default_udp_probe,
    loopback_targets: Sequence[tuple[str, int]] = (("127.0.0.1", 1),),
    ipv6_target: tuple[str, int] | None = ("2606:4700:4700::1111", 443),
    required_services: Sequence[tuple[str, str, int]] = (),
    dns_probe: Callable[[], tuple[str, str]] | None = None,
    ipv6_available: Callable[[], bool] | None = None,
    policy_assertions: Mapping[str, str] | None = None,
    policy_identity: str = "",
    policy_sha256: str = "",
    interface_inventory: Sequence[str] = (),
    arm_id: str = "",
    rollout_id: str = "",
    workspace: str = "",
    executing_user: str = "",
    interpreter_path: str = "",
    interpreter_sha256: str = "",
    spawn_image_inventory: Sequence[str] = (),
    enforcement_scope: str = "",
    enforcement_capture_sha256: str = "",
) -> IsolationAttestation:
    """Observe every required dimension and build the attestation record.

    ``probe`` is injectable so the whole matrix is testable without opening a
    socket. Nothing here infers a verdict: it records outcomes and lets
    :func:`verify_isolation_attestation` recompute one.
    """
    pid = _safe_pid()
    probes: list[ProbeResult] = []

    def record(dim: str, target: str, proto: str, raw: str, detail: str, local: str) -> None:
        probes.append(
            ProbeResult(
                dimension=dim, target=target, protocol=proto,
                outcome=_classify(dim, raw), detail=detail, local_address=local,
                pid=pid, at=time.time(), raw_outcome=raw,
            )
        )

    # --- denied-required external dimensions ---
    # Destinations come from the caller (``--external``). They must be LITERAL
    # addresses: this runner must not depend on DNS to decide a block, and a name
    # that fails to resolve proves nothing about egress (see
    # ``default_egress_probe``). The destination port selects the acceptance
    # dimension so http / https / tcp are all represented.
    for host, port in external_targets:
        dim = _EXTERNAL_DIM_BY_PORT.get(port, "tcp")
        raw, detail, local = probe("tcp", host, port)
        record(dim, f"{host}:{port}", "tcp", raw, detail, local)

    # udp: a REAL datagram (send + observe), at a literal address so no name
    # resolution is involved. Silence is `unknown`, never a denial.
    u_host, u_port = udp_target
    raw, detail, local = udp_probe(u_host, u_port)
    record("udp", f"{u_host}:{u_port}", "udp", raw, detail, local)

    # --- alternate interface: the bypass a single-interface rule would miss ---
    # NOTE: this dimension is NOT observer-provable unprivileged. Whether every
    # non-loopback interface is covered is a property of the ENFORCED policy. An
    # unprivileged observer can only record the inventory it can see; the
    # coverage claim must come from the control that enforces it, and is carried
    # in `policy_assertions` rather than invented here.
    if interface_inventory:
        non_loopback = [i for i in interface_inventory if not _is_loopback_iface(i)]
        record(
            "alternate_interface",
            ";".join(non_loopback) or "(none reported)",
            "inventory",
            OUTCOME_UNKNOWN,
            "interface inventory observed; per-interface egress COVERAGE is not "
            "observer-provable and must be asserted by the enforced policy",
            "",
        )
    else:
        record("alternate_interface", "(not supplied)", "inventory", OUTCOME_UNKNOWN,
               "no interface inventory supplied", "")

    # --- proxy bypass: also a property of the enforced policy ---
    record(
        "proxy",
        "HTTP_PROXY/HTTPS_PROXY/ALL_PROXY bypass",
        "config",
        OUTCOME_UNKNOWN,
        "proxy-bypass denial is a property of the enforced policy; an "
        "unprivileged observer cannot assert it and must not",
        "",
    )

    # --- ipv6 ---
    if ipv6_available is not None and not ipv6_available():
        record("ipv6", "", "socket", OUTCOME_UNAVAILABLE,
               "no global IPv6 address configured on this host; the dimension is "
               "unavailable and is NOT counted as denied", "")
    elif ipv6_target is None:
        record("ipv6", "", "socket", OUTCOME_UNAVAILABLE, "no IPv6 target supplied", "")
    else:
        host, port = ipv6_target
        raw, detail, local = probe("tcp", host, port)
        record("ipv6", f"[{host}]:{port}", "tcp6", raw, detail, local)

    # --- dns behaviour must be declared, never inferred ---
    if dns_probe is None:
        record("dns", "", "config", OUTCOME_UNKNOWN,
               "DNS behaviour was not declared by the observer", "")
    else:
        outcome, detail = dns_probe()
        record("dns", "resolver", "dns", outcome, detail, "")

    # --- permitted-required: loopback and the declared local services ---
    for host, port in loopback_targets:
        raw, detail, local = probe("tcp", host, port)
        record("loopback", f"{host}:{port}", "tcp", raw, detail, local)
    for name, host, port in required_services:
        raw, detail, local = probe("tcp", host, port)
        record("required_service", f"{name} {host}:{port}", "tcp", raw, detail, local)
    if not required_services:
        record("required_service", "(none declared)", "inventory", OUTCOME_UNKNOWN,
               "no required local services were declared, so preservation of the "
               "services F2 needs is unproven", "")

    # --- negative control: must be reachable when NOT isolated ---
    negative = _negative_control(probe)
    neg_raw, neg_detail, _neg_local = negative
    neg_outcome = _classify("negative_control", neg_raw)

    return IsolationAttestation(
        schema=SCHEMA,
        arm_id=arm_id,
        rollout_id=rollout_id,
        workspace=workspace,
        observer_pid=pid,
        policy_identity=policy_identity,
        policy_sha256=policy_sha256,
        interface_inventory=tuple(interface_inventory),
        dns_behavior=(
            "declared" if (dns_probe is not None) else "undeclared"
        ),
        negative_control=neg_outcome,
        probes=tuple(probes),
        required_services=tuple(f"{n} {h}:{p}" for n, h, p in required_services),
        executing_user=executing_user,
        interpreter_path=interpreter_path,
        interpreter_sha256=interpreter_sha256,
        spawn_image_inventory=tuple(spawn_image_inventory),
        enforcement_scope=enforcement_scope,
        enforcement_capture_sha256=str(enforcement_capture_sha256 or ""),
        policy_assertions=tuple(
            sorted((str(k), str(v)) for k, v in (policy_assertions or {}).items())
        ),
    )


def _negative_control(
    probe: Callable[[str, str, int], tuple[str, str, str]]
) -> tuple[str, str, str]:
    """A probe that MUST succeed if the host is not egress-isolated.

    Without it, an over-block that also breaks loopback is indistinguishable from
    correct isolation, and a broken probe looks like a secure host.
    """
    return probe("tcp", "127.0.0.1", 1)


def _is_loopback_iface(name: str) -> bool:
    lowered = name.lower()
    return "loopback" in lowered or lowered.startswith("lo")


def _safe_pid() -> int:
    import os

    try:
        return int(os.getpid())
    except Exception:  # noqa: BLE001 - pragma: no cover
        return 0


@dataclass(frozen=True)
class VerificationVerdict:
    """Recomputed, fail-closed verdict over an attestation."""

    satisfied: bool
    satisfied_dimensions: tuple[str, ...]
    policy_declared_dimensions: tuple[str, ...]
    unproven_dimensions: tuple[str, ...]
    failed_dimensions: tuple[str, ...]
    unavailable_dimensions: tuple[str, ...]
    detail: str
    attestation_digest: str = ""
    # Dimensions whose ONLY observation was an ambiguous drop, satisfied by
    # re-deriving an explicit Deny from a host-supplied enforcement capture.
    # Kept apart from ``satisfied_dimensions`` so a reader can always tell an
    # observed refusal from an enforcement-attributed silent drop.
    enforcement_backed_dimensions: tuple[str, ...] = ()
    enforcement_capture_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "satisfied_dimensions": list(self.satisfied_dimensions),
            "policy_declared_dimensions": list(self.policy_declared_dimensions),
            "unproven_dimensions": list(self.unproven_dimensions),
            "failed_dimensions": list(self.failed_dimensions),
            "unavailable_dimensions": list(self.unavailable_dimensions),
            "enforcement_backed_dimensions": list(self.enforcement_backed_dimensions),
            "enforcement_capture_digest": self.enforcement_capture_digest,
            "detail": self.detail,
            "attestation_digest": self.attestation_digest,
        }

    def as_readiness_mapping(self) -> dict[str, str]:
        """Project onto the readiness checker's seven-string contract.

        Only genuinely ``denied`` dimensions become ``"denied"``.  Anything
        unobserved or unavailable becomes ``"unknown"``, which the readiness
        checker already treats as a failure -- so an incomplete observation can
        never be laundered into a pass.

        Enforcement-backed dimensions also project to ``"denied"``, because the
        capture derived an explicit Deny for them.  They are NOT silent about
        it: ``enforcement_backed_dimensions`` names them separately, and a
        caller that must distinguish the two kinds of evidence reads that list
        rather than this lossy projection.
        """
        out: dict[str, str] = {}
        for dim in ("http", "https", "tcp", "udp", "ipv6", "proxy"):
            if (
                dim in self.satisfied_dimensions
                or dim in self.policy_declared_dimensions
                or dim in self.enforcement_backed_dimensions
            ):
                out[dim] = OUTCOME_DENIED
            elif dim in self.unavailable_dimensions:
                out[dim] = OUTCOME_UNAVAILABLE
            else:
                out[dim] = OUTCOME_UNKNOWN
        out["loopback"] = (
            OUTCOME_PERMITTED if "loopback" in self.satisfied_dimensions else OUTCOME_UNKNOWN
        )
        return out



def _digest_of(att: IsolationAttestation) -> str:
    return hashlib.sha256(
        json.dumps(att.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def attestation_digest(att: IsolationAttestation) -> str:
    return _digest_of(att)


#: Enforcement scope models this repository recognises. Anything else is
#: recorded verbatim but is NOT accepted as a recognised control.
ENFORCEMENT_SCOPE_MODELS = (
    "windows_account",
    "firewall_program_path",
    "vm",
    "windows_sandbox",
)

#: Platform facts established from Microsoft documentation (2026-10-05) and
#: therefore asserted here rather than re-derived at run time.
PLATFORM_LIMITS = (
    "windows_firewall_does_not_filter_loopback",
    "firewall_program_rule_does_not_inherit_to_child_processes",
)


@dataclass(frozen=True)
class EnforcementVerdict:
    """Whether an attestation names a control that could have caused the denial."""

    bound: bool
    scope_model: str
    recognised_scope: bool
    executing_user: str
    interpreter_path: str
    interpreter_sha256: str
    spawn_image_count: int
    missing: tuple[str, ...]
    detail: str


def assess_enforcement_identity(
    att: IsolationAttestation | Mapping[str, Any],
) -> EnforcementVerdict:
    """Decide whether the attestation binds an *enforcement* identity.

    A probe result says only that a connection failed. It does not say which
    control made it fail, so without this an attestation is satisfied by a
    machine that happened to be offline. This fails closed: absence of any
    binding is ``NOT ESTABLISHED``, never "enforced".

    Two platform facts drive the required fields:

    * Windows Firewall cannot filter loopback, so loopback reachability is NOT
      evidence of egress enforcement -- an administrator must scope the control
      to the non-loopback path and say so here.
    * A ``-Program`` firewall rule matches one executable image and does NOT
      inherit to child processes, so the images the arm will spawn must be
      declared and matched individually by the control.
    """
    if isinstance(att, Mapping):
        att = parse_attestation(att)

    missing: list[str] = []
    if not att.enforcement_scope.strip():
        missing.append("enforcement_scope")
    if not att.interpreter_path.strip():
        missing.append("interpreter_path")
    if not att.interpreter_sha256.strip():
        missing.append("interpreter_sha256")

    recognised = att.enforcement_scope.strip() in ENFORCEMENT_SCOPE_MODELS
    if att.enforcement_scope.strip() and not recognised:
        missing.append("enforcement_scope(recognised)")

    if missing:
        detail = (
            "enforcement identity NOT ESTABLISHED -- missing "
            f"{', '.join(missing)}. A probe result records that a connection "
            "failed, not which control failed it."
        )
    else:
        detail = (
            f"enforcement identity bound via {att.enforcement_scope!r}: "
            f"user={att.executing_user or 'undeclared'} "
            f"interpreter={att.interpreter_path} "
            f"({att.interpreter_sha256[:12]}...) "
            f"spawn_images={len(att.spawn_image_inventory)}. "
            f"Platform limits: {'; '.join(PLATFORM_LIMITS)}."
        )

    return EnforcementVerdict(
        bound=not missing,
        scope_model=att.enforcement_scope,
        recognised_scope=recognised,
        executing_user=att.executing_user,
        interpreter_path=att.interpreter_path,
        interpreter_sha256=att.interpreter_sha256,
        spawn_image_count=len(att.spawn_image_inventory),
        missing=tuple(missing),
        detail=detail,
    )


def verify_isolation_attestation(
    att: IsolationAttestation | Mapping[str, Any],
    *,
    required_services: Sequence[str] = (),
    enforcement_capture: Mapping[str, Any] | bytes | None = None,
) -> VerificationVerdict:
    """Recompute the isolation verdict from the recorded evidence.

    Fails closed.  A dimension is ``satisfied`` only when a probe for it was
    actually recorded with the required outcome.  Absence is ``unproven``, never
    ``denied``.

    ``enforcement_capture`` is HOST-side evidence, supplied by the caller that
    independently read the enforcement artifact (never by the probed guest).
    When present and well-formed, a denied-required dimension whose sole
    observation is an ambiguous drop (``timeout``/``silent``) is satisfied only
    if the capture's own highest-weight matching outbound rule is an explicit
    ``Deny`` for that dimension's literal target, the attestation claims this
    exact capture's digest, and the capture is bound to a recognised ``vm``
    scope.  Such dimensions are reported in
    ``enforcement_backed_dimensions`` and never in ``satisfied_dimensions``.
    """
    if isinstance(att, Mapping):
        att = parse_attestation(att)

    digest = _digest_of(att)
    by_dim: dict[str, list[ProbeResult]] = {}
    for p in att.probes:
        by_dim.setdefault(p.dimension, []).append(p)

    satisfied: list[str] = []
    declared: list[str] = []
    unproven: list[str] = []
    failed: list[str] = []
    unavailable: list[str] = []
    enforcement_backed: list[str] = []
    enforcement_digest = ""

    # Assertions carried by the ENFORCED policy. These are recorded distinctly
    # from probe observations so a reader can always tell which dimensions were
    # measured and which were asserted by the control that enforces the boundary.
    asserted = dict(att.policy_assertions)

    # Enforcement-backed denial: accepted only when the HOST hands over the
    # capture, the capture hashes to the digest the attestation CLAIMED, and
    # the capture is bound to a recognised vm scope. A guest that supplied its
    # own capture (impossible through this API) or that claimed a digest that
    # does not match gets the strict contract instead.
    enforcement: dict[str, Any] | None = None
    if enforcement_capture is not None:
        # The digest must bind the capture EXACTLY as the host supplied it --
        # not a normalised copy -- or a legitimate capture carrying informative
        # extras (adapter name, switch) would never match its own claim.
        if isinstance(enforcement_capture, (bytes, bytearray)):
            try:
                supplied: Any = json.loads(bytes(enforcement_capture).decode("utf-8"))
            except Exception:  # noqa: BLE001 - parse_enforcement_capture reports it
                supplied = enforcement_capture
        else:
            supplied = enforcement_capture
        try:
            parsed_capture = parse_enforcement_capture(enforcement_capture)
        except ValueError as exc:
            unproven.append("enforcement_capture")
            enforced_reason = f"enforcement capture rejected: {exc}"
        else:
            claimed = str(att.enforcement_capture_sha256 or "")
            recomputed = (
                canonical_enforcement_digest(supplied)
                if isinstance(supplied, Mapping)
                else ""
            )
            if not claimed:
                enforced_reason = (
                    "attestation claims no enforcement capture digest, so a "
                    "host-supplied capture cannot be attributed to this observation"
                )
                unproven.append("enforcement_capture")
            elif claimed != recomputed:
                enforced_reason = (
                    f"attestation claims enforcement digest {claimed[:12]}... but the "
                    f"supplied capture hashes to {recomputed[:12]}..."
                )
                unproven.append("enforcement_capture")
            elif att.enforcement_scope.strip() != "vm":
                enforced_reason = (
                    f"enforcement_scope {att.enforcement_scope!r} is not 'vm'; a "
                    "drop-attributed denial requires a vm-scoped control"
                )
                unproven.append("enforcement_capture")
            else:
                enforcement = parsed_capture
                enforcement_digest = recomputed
                enforced_reason = (
                    f"enforcement capture accepted ({parsed_capture['kind']}, "
                    f"{len(parsed_capture['rules'])} rules, subject "
                    f"{parsed_capture['subject']['vm_name']})"
                )
    else:
        enforced_reason = (
            "no host-supplied enforcement capture; a denied-required dimension "
            "must be observed as a refusal"
        )

    for dim in sorted(DENIED_REQUIRED | PERMITTED_REQUIRED):
        results = by_dim.get(dim, [])
        outcomes = {r.outcome for r in results}
        want = OUTCOME_DENIED if dim in DENIED_REQUIRED else OUTCOME_PERMITTED
        contrary = OUTCOME_PERMITTED if dim in DENIED_REQUIRED else OUTCOME_DENIED

        # A CONTRADICTING observation is decisive, even mixed with unknown
        # results: a denied-required dimension that was seen permitted (or a
        # permitted-required one seen denied) FAILS. An assertion must never
        # erase observed reachable-egress evidence.
        if contrary in outcomes:
            failed.append(dim)
            continue

        # Satisfied only by a clean, unanimous required outcome.
        if results and outcomes == {want}:
            satisfied.append(dim)
            continue

        # Not satisfied. A policy assertion may satisfy ONLY the dimensions that
        # are genuinely NOT observer-provable -- never one with a non-unknown
        # observation -- and only with a well-formed identified policy digest.
        # An assertion is a declaration, never an observed network result.
        observed_any = bool(outcomes - {OUTCOME_UNKNOWN, OUTCOME_UNAVAILABLE})
        if dim in _POLICY_ASSERTABLE_DIMENSIONS and not observed_any:
            assertion = asserted.get(dim)
            if (assertion == want and att.policy_identity
                    and is_sha256_hex(att.policy_sha256)):
                declared.append(dim)
                continue

        # Enforcement-backed denial. Permitted only for a denied-required
        # dimension whose SOLE observation is an ambiguous drop, and only when
        # the capture's own rules derive an explicit Deny for every literal
        # target that dimension was probed with.
        if (
            dim in DENIED_REQUIRED
            and enforcement is not None
            and not observed_any
            and results
            and outcomes == {OUTCOME_UNKNOWN}
            # Only a DROP-SHAPED observation may be attributed to enforcement.
            # ``unreachable`` has a specific non-enforcement explanation (no
            # route: the packet never reached an enforcement point), and
            # ``dns_unresolved``/``error`` name their own alternatives, so none
            # of them may be laundered into an enforcement-attributed denial.
            and {p.raw_outcome for p in results} <= _AMBIGUOUS_RAW_OUTCOMES
            and all(p.raw_outcome for p in results)
        ):
            pairs = _dimension_targets_addressed(results, dim)
            if pairs and all(
                enforcement_denies(enforcement, address, proto) for address, proto in pairs
            ):
                enforcement_backed.append(dim)
                continue

        if results and outcomes <= {OUTCOME_UNAVAILABLE}:
            unavailable.append(dim)
        unproven.append(dim)

    # Binding: an attestation that cannot be tied to an arm proves nothing about
    # that arm.
    if not (att.arm_id and att.rollout_id and att.workspace):
        unproven.append("binding")

    # Negative control: if the probe methodology cannot demonstrate a reachable
    # destination, a "denied" reading is indistinguishable from a broken probe.
    # This gate is UNAFFECTED by enforcement-backed denial: a capture can never
    # stand in for evidence that the probe harness still works.
    if att.negative_control != OUTCOME_PERMITTED:
        unproven.append("negative_control")

    if att.dns_behavior != "declared":
        unproven.append("dns_behaviour")

    missing_services = [s for s in required_services if s not in att.required_services]
    if missing_services:
        unproven.append("required_service_coverage")

    ok = not unproven and not failed
    if ok:
        parts = [
            f"{len(satisfied)} required dimensions observed and satisfied",
            f"{len(declared)} asserted by the identified enforced policy",
        ]
        if enforcement_backed:
            parts.append(
                f"{len(enforcement_backed)} satisfied by enforcement-backed silent "
                f"drop ({enforcement_reason_short(enforcement_backed)}; "
                f"capture {enforcement_digest[:12]}...)"
            )
        parts.append("negative control reachable")
        parts.append(f"bound to {att.arm_id}/{att.rollout_id}")
        detail = ", ".join(parts)
    else:
        parts = []
        if failed:
            parts.append(f"FAILED (egress reachable): {failed}")
        if unproven:
            parts.append(f"UNPROVEN: {unproven}")
        if enforcement_backed:
            parts.append(
                f"policy-enforced silent drop (not observed): {enforcement_backed} "
                f"-- {enforced_reason}"
            )
        if declared:
            parts.append(f"policy-asserted (not observed): {declared}")
        if unavailable:
            parts.append(f"UNAVAILABLE (not counted as denied): {unavailable}")
        detail = "clean-room isolation NOT established -- " + "; ".join(parts)

    return VerificationVerdict(
        satisfied=ok,
        satisfied_dimensions=tuple(satisfied),
        policy_declared_dimensions=tuple(declared),
        unproven_dimensions=tuple(unproven),
        failed_dimensions=tuple(failed),
        unavailable_dimensions=tuple(unavailable),
        enforcement_backed_dimensions=tuple(enforcement_backed),
        enforcement_capture_digest=enforcement_digest,
        detail=detail,
        attestation_digest=digest,
    )


def enforcement_reason_short(dims: Sequence[str]) -> str:
    """Names the raw observation class behind an enforcement-backed dimension."""
    return f"{', '.join(sorted(dims))} observed only as timeout/silent"


def build_attestation(**kwargs: Any) -> IsolationAttestation:

    return run_egress_probes(**kwargs)


def render_attestation(att: IsolationAttestation) -> bytes:
    """Canonical deterministic bytes, safe to retain as evidence."""
    return json.dumps(
        att.to_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def parse_attestation(data: Mapping[str, Any] | bytes) -> IsolationAttestation:
    if isinstance(data, (bytes, bytearray)):
        try:
            data = json.loads(bytes(data).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"attestation is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError("attestation must be a JSON object")
    if data.get("schema") != SCHEMA:
        raise ValueError(f"attestation schema {data.get('schema')!r} != {SCHEMA!r}")
    try:
        probes = tuple(
            ProbeResult(
                dimension=str(p["dimension"]),
                target=str(p.get("target", "")),
                protocol=str(p.get("protocol", "")),
                outcome=str(p["outcome"]),
                detail=str(p.get("detail", "")),
                local_address=str(p.get("local_address", "")),
                elapsed_ms=int(p.get("elapsed_ms", 0)),
                pid=int(p.get("pid", 0)),
                at=float(p.get("at", 0.0)),
                raw_outcome=str(p.get("raw_outcome", "")),
            )
            for p in data.get("probes", [])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"attestation probe records are malformed: {exc}") from exc
    return IsolationAttestation(
        schema=SCHEMA,
        arm_id=str(data.get("arm_id", "")),
        rollout_id=str(data.get("rollout_id", "")),
        workspace=str(data.get("workspace", "")),
        observer_pid=int(data.get("observer_pid", 0)),
        policy_identity=str(data.get("policy_identity", "")),
        policy_sha256=str(data.get("policy_sha256", "")),
        interface_inventory=tuple(data.get("interface_inventory", ())),
        dns_behavior=str(data.get("dns_behavior", "undeclared")),
        negative_control=str(data.get("negative_control", "unknown")),
        probes=probes,
        required_services=tuple(data.get("required_services", ())),
        executing_user=str(data.get("executing_user", "")),
        interpreter_path=str(data.get("interpreter_path", "")),
        interpreter_sha256=str(data.get("interpreter_sha256", "")),
        spawn_image_inventory=tuple(data.get("spawn_image_inventory", ())),
        enforcement_scope=str(data.get("enforcement_scope", "")),
        enforcement_capture_sha256=str(data.get("enforcement_capture_sha256", "")),
        policy_assertions=tuple(
            (str(a["dimension"]), str(a["assertion"]))
            for a in data.get("policy_assertions", ())
            if isinstance(a, Mapping) and "dimension" in a and "assertion" in a
        ),
        notes=str(data.get("notes", "")),
    )


def host_summary() -> dict[str, Any]:
    """Non-privileged host facts worth recording with an attestation."""
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "hostname": platform.node(),
        "python": platform.python_version(),
    }


def _iter_dimensions(att: IsolationAttestation) -> Iterable[tuple[str, str]]:
    for p in att.probes:
        yield p.dimension, p.outcome


# --------------------------------------------------------------------------
# CLI. The operator runs this INSIDE the enforced egress policy, from inside the
# arm's execution context, and hands the resulting artifact to the readiness
# gate. It opens sockets and changes nothing.
# --------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    """Emit a clean-room attestation. Exit 0 when isolation is established.

    Deliberately does NOT accept a flag that declares dimensions denied. A
    declaration is possible only via ``--policy-assert``, which is recorded as a
    POLICY ASSERTION and reported separately from observed probes, and which is
    rejected unless ``--policy-identity`` and ``--policy-sha256`` are supplied.
    """
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(
        prog="python -m qwen_train.f2_isolation",
        description="Observe and verify F2 clean-room network isolation.",
    )
    ap.add_argument("--out", required=True, help="path to write the attestation JSON")
    ap.add_argument("--arm-id", default="", help="arm identity to bind to")
    ap.add_argument("--rollout-id", default="", help="rollout identity to bind to")
    ap.add_argument("--workspace", default="", help="arm workspace path to bind to")
    ap.add_argument("--enforcement-scope", default="",
                    help="how the egress control is scoped: " + "|".join(ENFORCEMENT_SCOPE_MODELS))
    ap.add_argument("--executing-user", default="",
                    help="Windows account the arm runs under (no value is inferred)")
    ap.add_argument("--interpreter-path", default="",
                    help="full path of the executable image that runs the arm")
    ap.add_argument("--interpreter-sha256", default="",
                    help="SHA-256 of that executable image")
    ap.add_argument("--spawn-image", action="append", default=[],
                    help="an additional executable image the arm will spawn (repeatable); "
                         "firewall -Program rules do NOT inherit to child processes")
    ap.add_argument("--policy-identity", default="",
                    help="identity of the ENFORCED policy (required for assertions)")
    ap.add_argument("--policy-sha256", default="",
                    help="sha256 of the enforced policy artifact")
    ap.add_argument("--policy-assert", action="append", default=[],
                    metavar="DIM=VALUE",
                    help="policy-declared outcome for a non-observer-provable "
                         "dimension (e.g. proxy=denied). Recorded as an assertion, "
                         "never as an observation.")
    ap.add_argument("--interface", action="append", default=[],
                    help="a non-loopback network interface name (repeatable)")
    ap.add_argument("--required-service", action="append", default=[],
                    metavar="NAME=HOST:PORT",
                    help="a local service that MUST remain reachable (repeatable)")
    ap.add_argument("--external", action="append", default=[],
                    metavar="HOST:PORT",
                    help="an external destination that must be denied (repeatable); "
                         "use a literal address, and the port selects http(80)/"
                         "https(443)/tcp(any other)")
    ap.add_argument("--udp", default="8.8.8.8:53", metavar="HOST:PORT",
                    help="the UDP destination to send one real datagram to "
                         "(default 8.8.8.8:53); use a literal address")
    ap.add_argument("--enforcement-digest", default="", metavar="SHA256",
                    help="SHA-256 of the HOST-captured enforcement artifact this "
                         "observation is made under. It is a CLAIM recorded in the "
                         "attestation; only the host-side verifier, which holds the "
                         "capture itself, can turn it into an enforcement-backed "
                         "denial. Omit to keep the strict observed-refusal contract.")
    args = ap.parse_args(list(argv) if argv is not None else None)

    assertions: dict[str, str] = {}
    for item in args.policy_assert:
        if "=" not in item:
            print(f"f2_isolation: bad --policy-assert {item!r}; expected DIM=VALUE")
            return 2
        dim, val = item.split("=", 1)
        assertions[dim.strip()] = val.strip()

    services: list[tuple[str, str, int]] = []
    for item in args.required_service:
        try:
            name, hostport = item.split("=", 1)
            host, port = hostport.rsplit(":", 1)
            services.append((name, host, int(port)))
        except Exception:  # noqa: BLE001
            print(f"f2_isolation: bad --required-service {item!r}; expected NAME=HOST:PORT")
            return 2

    externals: list[tuple[str, int]] = []
    for item in args.external:
        try:
            host, port = item.rsplit(":", 1)
            externals.append((host, int(port)))
        except Exception:  # noqa: BLE001
            print(f"f2_isolation: bad --external {item!r}; expected HOST:PORT")
            return 2

    try:
        _uh, _up = args.udp.rsplit(":", 1)
        udp_target = (_uh, int(_up))
    except Exception:  # noqa: BLE001
        print(f"f2_isolation: bad --udp {args.udp!r}; expected HOST:PORT")
        return 2

    def dns_probe() -> tuple[str, str]:
        # NOT ``default_egress_probe``: a UDP ``connect()`` only assigns a
        # default peer and transmits nothing, so it reports ``permitted`` for
        # any routable address even when every datagram is dropped.  That
        # recorded a DNS reachability the probe never demonstrated.  A real
        # datagram is the only honest observation.
        return default_udp_probe("8.8.8.8", 53)[:2]

    def ipv6_available() -> bool:
        # NOT ``getaddrinfo`` on a literal: that succeeds whether or not this
        # host has any IPv6 path, so it reported "IPv6 available" on a guest
        # holding only a link-local address and no default route, and the probe
        # then recorded a misleading ``unreachable``.  The dimension is about a
        # configured global IPv6 ADDRESS -- the thing an egress leak would
        # actually leave through.  Absent one, the dimension is `unavailable`,
        # which the contract already refuses to count as `denied`.
        from ipaddress import IPv6Address

        try:
            infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6)
        except Exception:  # noqa: BLE001 - fail closed: no IPv6 path we can see
            return False
        for info in infos:
            try:
                ip = IPv6Address(str(info[4][0]).split("%", 1)[0])
            except ValueError:  # noqa: BLE001 - skip anything unparsable
                continue
            if not (ip.is_link_local or ip.is_loopback or ip.is_unspecified):
                return True
        return False

    try:
        att = run_egress_probes(
            external_targets=tuple(externals) or DEFAULT_EXTERNAL_TARGETS,
            udp_target=udp_target,
            loopback_targets=(("127.0.0.1", 1),),
            required_services=tuple(services),
            dns_probe=dns_probe,
            ipv6_available=ipv6_available,
            interface_inventory=tuple(args.interface),
            policy_assertions=assertions,
            policy_identity=args.policy_identity,
            policy_sha256=args.policy_sha256,
            arm_id=args.arm_id,
            rollout_id=args.rollout_id,
            workspace=args.workspace,
            executing_user=args.executing_user,
            interpreter_path=args.interpreter_path,
            interpreter_sha256=args.interpreter_sha256,
            spawn_image_inventory=tuple(args.spawn_image),
            enforcement_scope=args.enforcement_scope,
            enforcement_capture_sha256=args.enforcement_digest,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"f2_isolation: probe run failed: {type(exc).__name__}: {exc}")
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    blob = render_attestation(att)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(blob)
    import os as _os

    _os.replace(tmp, out)

    verdict = verify_isolation_attestation(att)
    print(_json.dumps(verdict.to_dict(), sort_keys=True, indent=2))
    print(f"\nattestation written: {out}")
    print(f"attestation digest : {verdict.attestation_digest}")
    if args.enforcement_digest:
        print(
            "enforcement claim  : recorded as a claim only. This run holds no "
            "enforcement capture, so the verdict above is the strict "
            "observed-refusal verdict; the host-side verifier that holds the "
            "capture decides whether the claim is backed."
        )
    if verdict.enforcement_backed_dimensions:
        print(
            "NOTE: enforcement-backed dimensions are NOT counted as "
            "satisfied_dimensions and never stand in for the negative control."
        )
    if not verdict.satisfied:
        print(f"NOT ESTABLISHED (guest-side, strict contract): {verdict.detail}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
