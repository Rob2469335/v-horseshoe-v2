"""Defense-in-depth network blocker for pytest test collection.

IMPORTANT SECURITY NOTICE (Section 4A):
This Python-level socket hook is defense-in-depth only. It is NOT a full
operating-system security boundary against hostile Python code that can
bypass Python-level monkeypatches (e.g. ``ctypes`` into libc, subprocesses,
or a pre-imported socket object). Full network containment requires OS-level
firewall rules, Windows network filtering, or container/VM network isolation.
"""
from __future__ import annotations

import ipaddress
import socket

_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_CONNECT_EX = socket.socket.connect_ex
_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_ORIGINAL_GETHOSTBYNAME = socket.gethostbyname
_ORIGINAL_GETHOSTBYNAME_EX = socket.gethostbyname_ex
_ORIGINAL_GETHOSTBYADDR = socket.gethostbyaddr
_ORIGINAL_GETFQDN = socket.getfqdn
_ORIGINAL_SENDTO = socket.socket.sendto
_ORIGINAL_SENDMSG = getattr(socket.socket, "sendmsg", None)


class CollectionNetworkForbidden(OSError):
    """Raised when collection code attempts an outbound network operation."""


def _as_host(address: object) -> str | None:
    """Extract the host from a socket address or host string.

    Accepts ``(host, port)``/``(host, port, flow, scope)`` tuples, a bare host
    string, or ``None`` (bind/local operations).
    """
    if address is None:
        return None
    if isinstance(address, (tuple, list)):
        if not address:
            return None
        return str(address[0])
    if isinstance(address, (str, bytes)):
        return address.decode() if isinstance(address, bytes) else address
    return None


def _loopback_literal(host: str) -> bool:
    """True only for the literal loopback host or an IP literal in 127/8 or ::1.

    A *hostname* is never loopback here, even if it starts with ``127.``: the
    previous prefix test let ``127.attacker.example`` through, and
    ``create_connection`` would then resolve it and connect to the result.
    """
    h = host.strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _is_loopback(address: object) -> bool:
    host = _as_host(address)
    return host is not None and _loopback_literal(host)


def _blocked_connect(self, address):
    if not _is_loopback(address):
        raise CollectionNetworkForbidden(
            f"PROBE_COLLECTION_NETWORK_FORBIDDEN: outbound connection to {address} blocked"
        )
    return _ORIGINAL_CONNECT(self, address)


def _blocked_connect_ex(self, address):
    if not _is_loopback(address):
        return 10013  # WSAEACCES / Permission denied
    return _ORIGINAL_CONNECT_EX(self, address)


def _blocked_create_connection(address, *args, **kwargs):
    if not _is_loopback(address):
        raise CollectionNetworkForbidden(
            f"PROBE_COLLECTION_NETWORK_FORBIDDEN: outbound connection to {address} blocked"
        )
    return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)


def _blocked_getaddrinfo(host, port, *args, **kwargs):
    """Refuse name resolution for anything that is not a loopback literal.

    Blocking only ``connect`` leaves DNS exfiltration open: a test can encode
    data in a lookup even if the connection is later refused. ``localhost`` and
    loopback IP literals remain resolvable.
    """
    if host is None:
        return _ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)
    h = _as_host(host)
    if h is not None and _loopback_literal(h):
        return _ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)
    raise CollectionNetworkForbidden(
        f"PROBE_COLLECTION_NETWORK_FORBIDDEN: name resolution for {host!r} blocked"
    )


def _blocked_gethostbyname(hostname):
    h = _as_host(hostname)
    if h is not None and _loopback_literal(h):
        return _ORIGINAL_GETHOSTBYNAME(hostname)
    raise CollectionNetworkForbidden(
        f"PROBE_COLLECTION_NETWORK_FORBIDDEN: name resolution for {hostname!r} blocked"
    )


def _blocked_gethostbyname_ex(hostname):
    h = _as_host(hostname)
    if h is not None and _loopback_literal(h):
        return _ORIGINAL_GETHOSTBYNAME_EX(hostname)
    raise CollectionNetworkForbidden(
        f"PROBE_COLLECTION_NETWORK_FORBIDDEN: name resolution for {hostname!r} blocked"
    )


def _blocked_gethostbyaddr(ip_address):
    h = _as_host(ip_address)
    if h is not None and _loopback_literal(h):
        return _ORIGINAL_GETHOSTBYADDR(ip_address)
    raise CollectionNetworkForbidden(
        f"PROBE_COLLECTION_NETWORK_FORBIDDEN: reverse lookup for {ip_address!r} blocked"
    )


def _blocked_getfqdn(name=""):
    # ``getfqdn("")`` resolves the local host; ``getfqdn("name")`` is outbound
    # unless the name is a loopback literal.
    if name in ("", None):
        return _ORIGINAL_GETFQDN(name)
    h = _as_host(name)
    if h is not None and _loopback_literal(h):
        return _ORIGINAL_GETFQDN(name)
    raise CollectionNetworkForbidden(
        f"PROBE_COLLECTION_NETWORK_FORBIDDEN: name resolution for {name!r} blocked"
    )


def _blocked_sendto(self, data, *args, **kwargs):
    address = kwargs.get("address", args[-1] if args else None)
    if not _is_loopback(address):
        raise CollectionNetworkForbidden(
            f"PROBE_COLLECTION_NETWORK_FORBIDDEN: outbound datagram to {address} blocked"
        )
    return _ORIGINAL_SENDTO(self, data, *args, **kwargs)


def _blocked_sendmsg(self, data, *args, **kwargs):
    address = kwargs.get("address", args[-1] if args else None)
    if address is not None and not _is_loopback(address):
        raise CollectionNetworkForbidden(
            f"PROBE_COLLECTION_NETWORK_FORBIDDEN: outbound datagram to {address} blocked"
        )
    return _ORIGINAL_SENDMSG(self, data, *args, **kwargs)


def install_network_blocker() -> None:
    socket.socket.connect = _blocked_connect
    socket.socket.connect_ex = _blocked_connect_ex
    socket.create_connection = _blocked_create_connection
    socket.getaddrinfo = _blocked_getaddrinfo
    socket.gethostbyname = _blocked_gethostbyname
    socket.gethostbyname_ex = _blocked_gethostbyname_ex
    socket.gethostbyaddr = _blocked_gethostbyaddr
    socket.getfqdn = _blocked_getfqdn
    socket.socket.sendto = _blocked_sendto
    if _ORIGINAL_SENDMSG is not None:
        socket.socket.sendmsg = _blocked_sendmsg


def uninstall_network_blocker() -> None:
    socket.socket.connect = _ORIGINAL_CONNECT
    socket.socket.connect_ex = _ORIGINAL_CONNECT_EX
    socket.create_connection = _ORIGINAL_CREATE_CONNECTION
    socket.getaddrinfo = _ORIGINAL_GETADDRINFO
    socket.gethostbyname = _ORIGINAL_GETHOSTBYNAME
    socket.gethostbyname_ex = _ORIGINAL_GETHOSTBYNAME_EX
    socket.gethostbyaddr = _ORIGINAL_GETHOSTBYADDR
    socket.getfqdn = _ORIGINAL_GETFQDN
    socket.socket.sendto = _ORIGINAL_SENDTO
    if _ORIGINAL_SENDMSG is not None:
        socket.socket.sendmsg = _ORIGINAL_SENDMSG


def pytest_configure(config) -> None:
    """Pytest plugin hook to ensure network blocker is installed."""
    install_network_blocker()


# Auto-install when imported by pytest (-p _pytest_no_network)
install_network_blocker()
