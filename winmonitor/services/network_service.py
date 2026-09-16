"""Port and connection queries.

Like :mod:`winmonitor.services.process_service` these are pure functions over a
collected snapshot, so port lookup and developer-port detection can be tested
without binding real sockets.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import ConnectionInfo, PortInfo, is_developer_port

__all__ = [
    "connections_for_pid",
    "count_listening",
    "developer_ports",
    "filter_connections",
    "filter_ports",
    "find_port",
    "ports_for_pid",
    "sort_ports",
    "to_ports",
]


def to_ports(
    connections: Iterable[ConnectionInfo],
    listening_only: bool = True,
) -> list[PortInfo]:
    """Project connections onto their local endpoints.

    With ``listening_only`` the result is what most people mean by "used
    ports": server sockets, deduplicated per protocol/address/port/owner.
    """
    ports: list[PortInfo] = []
    seen: set[tuple[str, str, int, int | None]] = set()
    for connection in connections:
        if listening_only and not connection.is_listening:
            continue
        identity = (
            connection.protocol,
            connection.local_address,
            connection.local_port,
            connection.pid,
        )
        if listening_only:
            if identity in seen:
                continue
            seen.add(identity)
        ports.append(
            PortInfo(
                protocol=connection.protocol,
                local_address=connection.local_address,
                local_port=connection.local_port,
                remote_address=connection.remote_address,
                remote_port=connection.remote_port,
                state=connection.state,
                pid=connection.pid,
                process_name=connection.process_name,
                process_create_time=connection.process_create_time,
                executable=connection.executable,
                listening=connection.is_listening,
            )
        )
    return ports


def find_port(
    connections: Iterable[ConnectionInfo],
    port: int,
    protocol: str | None = None,
    listening_only: bool = True,
) -> list[PortInfo]:
    """Answer "who is using port N?".

    Returns every endpoint bound to ``port``.  More than one row is normal: a
    server usually listens on both the IPv4 and the IPv6 wildcard address.
    """
    wanted = protocol.upper() if protocol else None
    matches = [
        connection
        for connection in connections
        if connection.local_port == port
        and (wanted is None or connection.protocol == wanted)
        and (not listening_only or connection.is_listening)
    ]
    return to_ports(matches, listening_only=listening_only)


def ports_for_pid(
    connections: Iterable[ConnectionInfo],
    pid: int,
    listening_only: bool = False,
) -> list[PortInfo]:
    """Answer the reverse question: which ports does this process hold?"""
    matches = [connection for connection in connections if connection.pid == pid]
    return to_ports(matches, listening_only=listening_only)


def connections_for_pid(connections: Iterable[ConnectionInfo], pid: int) -> list[ConnectionInfo]:
    """Return every connection owned by ``pid``."""
    return [connection for connection in connections if connection.pid == pid]


def developer_ports(ports: Iterable[PortInfo]) -> list[PortInfo]:
    """Return the active endpoints that sit on well known development ports.

    Deduplicated per port, protocol and owner: a server listening on both the
    IPv4 and the IPv6 wildcard is one entry in this view, not two.  The
    wildcard binding is preferred as the representative row because that is the
    address a developer connects to.
    """
    best: dict[tuple[int, str, int | None], PortInfo] = {}
    for port in ports:
        if not is_developer_port(port.local_port):
            continue
        identity = (port.local_port, port.protocol, port.pid)
        current = best.get(identity)
        if current is None or _wildcard_rank(port) < _wildcard_rank(current):
            best[identity] = port
    return sorted(best.values(), key=lambda port: (port.local_port, port.protocol))


def _wildcard_rank(port: PortInfo) -> int:
    """Rank addresses so the most representative binding wins a tie."""
    address = port.local_address
    if address in ("0.0.0.0", "::"):
        return 0
    if address in ("127.0.0.1", "::1"):
        return 1
    return 2


def filter_ports(ports: Iterable[PortInfo], query: str = "") -> list[PortInfo]:
    """Free text filter over port number, process name, address and state."""
    needle = query.strip().lower()
    if not needle:
        return list(ports)
    result = []
    for port in ports:
        haystacks = (
            str(port.local_port),
            port.protocol.lower(),
            (port.process_name or "").lower(),
            port.local_address.lower(),
            (port.state or "").lower(),
            str(port.pid or ""),
            (port.service or "").lower(),
        )
        if any(needle in haystack for haystack in haystacks):
            result.append(port)
    return result


def filter_connections(
    connections: Iterable[ConnectionInfo], query: str = ""
) -> list[ConnectionInfo]:
    """Free text filter over both endpoints, the state and the owning process."""
    needle = query.strip().lower()
    if not needle:
        return list(connections)
    result = []
    for connection in connections:
        haystacks = (
            str(connection.local_port),
            str(connection.remote_port or ""),
            connection.protocol.lower(),
            (connection.process_name or "").lower(),
            connection.local_address.lower(),
            (connection.remote_address or "").lower(),
            (connection.state or "").lower(),
            str(connection.pid or ""),
        )
        if any(needle in haystack for haystack in haystacks):
            result.append(connection)
    return result


def sort_ports(ports: Iterable[PortInfo], by_port: bool = True) -> list[PortInfo]:
    """Sort by port number (the default) or by owning process name."""
    if by_port:
        return sorted(ports, key=lambda port: (port.local_port, port.protocol, port.pid or 0))
    return sorted(ports, key=lambda port: ((port.process_name or "~").lower(), port.local_port))


def count_listening(connections: Iterable[ConnectionInfo]) -> int:
    """Count distinct listening ports, not distinct sockets.

    A server bound to ``0.0.0.0:8080`` and ``[::]:8080`` is one port in use.
    """
    return len({(c.protocol, c.local_port) for c in connections if c.is_listening})
