"""Governed Experiment-J F2 runtime isolation guard.

One authoritative, testable home for the F2 governed-execution invariants:

* :func:`governed_f2` -- is THIS process a governed F2 P2 backend?
* :func:`assert_forbidden_services_unreachable` -- fail closed unless Qdrant and
  the embedding service are unreachable from THIS process.
* :data:`F2_FORBIDDEN_AGENT_TOOLS` / :func:`strip_forbidden_f2_tools` -- the
  Qdrant-backed agent capabilities that must not be delivered to a governed arm.

Scope: F2 engineering isolation only. This module does not touch any F0 science
definition (T/X/C0, pairing, endpoint, provenance, receipts). Availability of
these services is an ENVIRONMENT decision applied identically to both arms.

Nothing here starts, stops, or reconfigures a service. The probe is a bounded
connection attempt: *refused / unreachable is success* for the isolation
invariant, *reachable is a violation*.
"""

from __future__ import annotations

import errno
import os
import socket
from dataclasses import dataclass
from typing import Iterable, Mapping

#: Environment variable that marks a process as a governed F2 P2 backend.
F2_ISOLATION_ENV = "SWARM_F2_ISOLATION"

#: Host loopback services a governed F2 P2 must NOT be able to reach. Values are
#: (host, port); the loopback host is the CURRENT host-service binding. In the
#: VM topology these are unreachable because they live on the host, not in the
#: guest; on a same-host layout this assertion is what fails closed.
F2_FORBIDDEN_ENDPOINTS: Mapping[str, tuple[str, int]] = {
    "qdrant": ("127.0.0.1", 6333),
    "embedding": ("127.0.0.1", 8081),
}

#: Qdrant-backed agent capabilities removed from the governed F2 tool surface.
F2_FORBIDDEN_AGENT_TOOLS: tuple[str, ...] = (
    "semantic_search",   # queries the codebase_index collection
    "remember",          # writes long-term memory
    "deprecate_memory",  # mutates long-term memory
)

#: Probe statuses. A CHECK_ERROR is never success.
EXPECTED_UNREACHABLE = "EXPECTED_UNREACHABLE"
REACHABLE_VIOLATION = "REACHABLE_VIOLATION"
CHECK_ERROR = "CHECK_ERROR"

# errnos that mean "no route / refused" -- i.e. the isolation invariant holds.
_UNREACHABLE_ERRNOS = frozenset(
    {
        errno.ECONNREFUSED,
        errno.ETIMEDOUT,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        errno.EHOSTDOWN,
        errno.EADDRNOTAVAIL,
    }
)


class F2IsolationViolation(RuntimeError):
    """A governed F2 process can reach (or cannot rule out) a forbidden service."""


@dataclass(frozen=True)
class ProbeResult:
    name: str
    host: str
    port: int
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == EXPECTED_UNREACHABLE


def governed_f2() -> bool:
    """True when this process is a governed Experiment-J F2 P2 backend.

    Derived ONLY from ``SWARM_F2_ISOLATION=1`` -- an explicit, F2-scoped marker
    set by the F2 P2 launcher. It is never inferred from unrelated globals, so
    normal Swarm OS startup is unchanged.
    """
    return os.environ.get(F2_ISOLATION_ENV, "").strip() == "1"


def probe_endpoint(name: str, host: str, port: int, timeout: float = 0.5) -> ProbeResult:
    """Bounded connection probe classifying reachability of one endpoint."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ProbeResult(name, host, port, REACHABLE_VIOLATION, "connected")
    except TimeoutError as exc:  # includes socket.timeout (alias since 3.10)
        return ProbeResult(name, host, port, EXPECTED_UNREACHABLE, type(exc).__name__)
    except ConnectionRefusedError as exc:
        return ProbeResult(name, host, port, EXPECTED_UNREACHABLE, type(exc).__name__)
    except OSError as exc:
        if exc.errno in _UNREACHABLE_ERRNOS or exc.errno is None:
            return ProbeResult(
                name, host, port, EXPECTED_UNREACHABLE, f"OSError(errno={exc.errno})"
            )
        return ProbeResult(
            name, host, port, CHECK_ERROR, f"{type(exc).__name__}(errno={exc.errno}): {exc}"
        )


def check_forbidden_services_unreachable(
    endpoints: Mapping[str, tuple[str, int]] | None = None,
    timeout: float = 0.5,
) -> list[ProbeResult]:
    """Probe every forbidden endpoint; return results without raising."""
    eps = F2_FORBIDDEN_ENDPOINTS if endpoints is None else endpoints
    return [
        probe_endpoint(name, host, port, timeout) for name, (host, port) in eps.items()
    ]


def assert_forbidden_services_unreachable(
    endpoints: Mapping[str, tuple[str, int]] | None = None,
    timeout: float = 0.5,
) -> list[ProbeResult]:
    """Fail closed unless every forbidden endpoint is unreachable.

    Raises :class:`F2IsolationViolation` when any endpoint is reachable OR its
    state could not be determined (CHECK_ERROR). Returns the probe results on
    success so the caller can record the evidence (never credentials).
    """
    results = check_forbidden_services_unreachable(endpoints, timeout)
    bad = [r for r in results if not r.ok]
    if bad:
        detail = "; ".join(f"{r.name}={r.status}({r.detail})" for r in bad)
        raise F2IsolationViolation(
            "F2 fail-closed: forbidden service reachable or uncheckable from "
            f"governed P2: {detail}"
        )
    return results


def strip_forbidden_f2_tools(allowed: Iterable[str] | None) -> list[str] | None:
    """Remove Qdrant-backed capabilities from the governed F2 tool surface.

    No-op unless this is a governed F2 process, and no-op for an empty/None list
    (preserving the existing "no restriction => unchanged" contract).
    """
    if not allowed:
        return allowed  # type: ignore[return-value]
    if not governed_f2():
        return list(allowed)
    return [t for t in allowed if t not in F2_FORBIDDEN_AGENT_TOOLS]
