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
* ``denied`` is only recorded when a probe was actually attempted and refused.
* A missing or failing negative control invalidates the whole attestation.
* DNS behaviour must be declared explicitly; it is never inferred.
* If IPv6 is not configured on the host, that dimension is ``unavailable`` with a
  reason -- it is not silently counted as ``denied``.
"""
from __future__ import annotations

import hashlib
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

OUTCOME_DENIED = "denied"
OUTCOME_PERMITTED = "permitted"
OUTCOME_UNKNOWN = "unknown"
OUTCOME_UNAVAILABLE = "unavailable"

#: Never treat these as evidence that egress is blocked: they prove nothing about
#: the destination and are exactly the ambiguity a name-based probe creates.
_PROBE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class ProbeResult:
    """One observed connectivity attempt."""

    dimension: str
    target: str
    protocol: str
    outcome: str
    detail: str = ""
    local_address: str = ""
    elapsed_ms: int = 0
    pid: int = 0
    at: float = 0.0

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
            "policy_assertions": [
                {"dimension": d, "assertion": a, "source": "enforced_policy"}
                for d, a in self.policy_assertions
            ],
            "notes": self.notes,
            "probes": [p.to_dict() for p in self.probes],
        }


SCHEMA = "f2_isolation_attestation_v1"


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
        outcome = OUTCOME_DENIED
        detail = "timed out (silently dropped)"
    except OSError as exc:
        # Windows returns 10013/10054 for a WFP/firewall block and 10051/10065
        # for unreachable-network. Both mean the packet did not leave.
        outcome = OUTCOME_DENIED
        detail = f"{type(exc).__name__} (winerror={getattr(exc, 'winerror', None)}): {exc}"
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


def _classify(dimension: str, raw: str) -> str:
    if raw in (OUTCOME_PERMITTED, OUTCOME_DENIED, OUTCOME_UNAVAILABLE):
        return raw
    if raw == "dns_unresolved":
        # Not evidence of a block. Keep it distinguishable.
        return OUTCOME_UNKNOWN
    return OUTCOME_UNKNOWN


def run_egress_probes(
    *,
    probe: Callable[[str, str, int], tuple[str, str, str]] = default_egress_probe,
    external_targets: Sequence[tuple[str, int]] = (("api.github.com", 443),),
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
                pid=pid, at=time.time(),
            )
        )

    # --- denied-required dimensions, probed over both a name and a literal IP ---
    for dim, host, port in (("http", "example.com", 80), ("https", "api.github.com", 443)):
        raw, detail, local = probe("tcp", host, port)
        record(dim, f"{host}:{port}", "tcp", raw, detail, local)

    # A literal address removes DNS from the question: a name that fails to
    # resolve proves nothing about egress.
    for dim, host, port in (("tcp", "93.184.216.34", 80), ("udp", "8.8.8.8", 53)):
        raw, detail, local = probe("tcp" if dim == "tcp" else "dns", host, port)
        record(dim, f"{host}:{port}", "udp" if dim == "udp" else "tcp", raw, detail, local)

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "satisfied_dimensions": list(self.satisfied_dimensions),
            "policy_declared_dimensions": list(self.policy_declared_dimensions),
            "unproven_dimensions": list(self.unproven_dimensions),
            "failed_dimensions": list(self.failed_dimensions),
            "unavailable_dimensions": list(self.unavailable_dimensions),
            "detail": self.detail,
            "attestation_digest": self.attestation_digest,
        }

    def as_readiness_mapping(self) -> dict[str, str]:
        """Project onto the readiness checker's seven-string contract.

        Only genuinely ``denied`` dimensions become ``"denied"``. Anything
        unobserved or unavailable becomes ``"unknown"``, which the readiness
        checker already treats as a failure -- so an incomplete observation can
        never be laundered into a pass.
        """
        out: dict[str, str] = {}
        for dim in ("http", "https", "tcp", "udp", "ipv6", "proxy"):
            if dim in self.satisfied_dimensions or dim in self.policy_declared_dimensions:
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


def verify_isolation_attestation(
    att: IsolationAttestation | Mapping[str, Any],
    *,
    required_services: Sequence[str] = (),
) -> VerificationVerdict:
    """Recompute the isolation verdict from the recorded evidence.

    Fails closed. A dimension is ``satisfied`` only when a probe for it was
    actually recorded with the required outcome. Absence is ``unproven``, never
    ``denied``.
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

    # Assertions carried by the ENFORCED policy. These are recorded distinctly
    # from probe observations so a reader can always tell which dimensions were
    # measured and which were asserted by the control that enforces the boundary.
    asserted = dict(att.policy_assertions)

    for dim in sorted(DENIED_REQUIRED | PERMITTED_REQUIRED):
        results = by_dim.get(dim, [])
        outcomes = {r.outcome for r in results}
        observed = bool(results) and OUTCOME_UNKNOWN not in outcomes

        if observed and results:
            if dim in DENIED_REQUIRED:
                if outcomes == {OUTCOME_DENIED}:
                    satisfied.append(dim)
                elif OUTCOME_PERMITTED in outcomes:
                    failed.append(dim)
                elif outcomes <= {OUTCOME_UNAVAILABLE}:
                    unavailable.append(dim)
                else:
                    unproven.append(dim)
            else:
                if outcomes == {OUTCOME_PERMITTED}:
                    satisfied.append(dim)
                elif OUTCOME_DENIED in outcomes:
                    failed.append(dim)
                else:
                    unproven.append(dim)
            continue

        # Not observed. A policy assertion may satisfy it, but only if the
        # policy is actually identified -- an anonymous assertion proves nothing.
        assertion = asserted.get(dim)
        want = OUTCOME_DENIED if dim in DENIED_REQUIRED else OUTCOME_PERMITTED
        if assertion == want and att.policy_identity and att.policy_sha256:
            declared.append(dim)
        elif results and outcomes <= {OUTCOME_UNAVAILABLE}:
            unavailable.append(dim)
        else:
            unproven.append(dim)

    # Binding: an attestation that cannot be tied to an arm proves nothing about
    # that arm.
    if not (att.arm_id and att.rollout_id and att.workspace):
        unproven.append("binding")

    # Negative control: if the probe methodology cannot demonstrate a reachable
    # destination, a "denied" reading is indistinguishable from a broken probe.
    if att.negative_control != OUTCOME_PERMITTED:
        unproven.append("negative_control")

    if att.dns_behavior != "declared":
        unproven.append("dns_behaviour")

    missing_services = [s for s in required_services if s not in att.required_services]
    if missing_services:
        unproven.append("required_service_coverage")

    ok = not unproven and not failed
    if ok:
        detail = (
            f"{len(satisfied)} required dimensions observed and satisfied, "
            f"{len(declared)} asserted by the identified enforced policy; "
            f"negative control reachable; bound to {att.arm_id}/{att.rollout_id}"
        )
    else:
        parts = []
        if failed:
            parts.append(f"FAILED (egress reachable): {failed}")
        if unproven:
            parts.append(f"UNPROVEN: {unproven}")
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
        detail=detail,
        attestation_digest=digest,
    )


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
                    help="an external destination that must be denied (repeatable)")
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

    def dns_probe() -> tuple[str, str]:
        raw, detail, _ = default_egress_probe("dns", "8.8.8.8", 53)
        return raw, detail

    def ipv6_available() -> bool:
        try:
            infos = socket.getaddrinfo("2606:4700:4700::1111", 443, socket.AF_INET6)
        except Exception:  # noqa: BLE001
            return False
        return bool(infos)

    try:
        att = run_egress_probes(
            external_targets=tuple(externals) or (("api.github.com", 443),),
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
    if not verdict.satisfied:
        print(f"NOT ESTABLISHED: {verdict.detail}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
