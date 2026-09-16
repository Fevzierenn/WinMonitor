"""Network connection and port enumeration.

Three layers, tried in order:

1. ``psutil.net_connections`` - the primary path.  On Windows psutil calls
   ``GetExtendedTcpTable``/``GetExtendedUdpTable`` internally.
2. :mod:`winmonitor.utils.windows` - the same two Windows APIs driven directly
   through ``ctypes``, used when psutil raises ``AccessDenied`` (which happens
   on hardened systems and under some security products).
3. ``netstat -ano`` - a last resort, and the only place in WinMonitor where a
   shell command is used at all.  It is parsed defensively and never replaces a
   successful API call.

The layer that produced a snapshot is recorded in :attr:`NetworkCollector.source`
so the UI can tell the user when it is running on degraded data.
"""

from __future__ import annotations

import logging
import socket
import subprocess
from collections.abc import Callable
from typing import Literal

import psutil

from ..models import ConnectionInfo
from ..utils import windows

logger = logging.getLogger(__name__)

__all__ = ["NetworkCollector", "ProcessLookup"]

#: Given a PID, return ``(name, create_time, executable)`` or ``None``.
ProcessLookup = Callable[[int], tuple[str | None, float | None, str | None] | None]

Source = Literal["psutil", "windows-api", "netstat", "unavailable"]

_FAMILY_NAMES = {int(socket.AF_INET): "IPv4", int(socket.AF_INET6): "IPv6"}

#: ``netstat`` prints these protocol tokens.
_NETSTAT_PROTOCOLS = {"TCP", "UDP", "TCPV6", "UDPV6"}

#: ``netstat`` spells a few states differently from psutil and iphlpapi; the
#: rest of the application only ever sees the psutil spelling.
_NETSTAT_STATES = {
    "LISTENING": "LISTEN",
    "SYN_RECEIVED": "SYN_RECV",
    "FIN_WAIT_1": "FIN_WAIT1",
    "FIN_WAIT_2": "FIN_WAIT2",
    "CLOSED": "CLOSE",
}


class NetworkCollector:
    """Collects TCP/UDP endpoints and attaches owning process details."""

    def __init__(
        self,
        process_lookup: ProcessLookup | None = None,
        show_tcp: bool = True,
        show_udp: bool = True,
        allow_netstat_fallback: bool = True,
    ) -> None:
        """Create a collector.

        Args:
            process_lookup: Resolves a PID to process details.  Supplying the
                process collector cache here avoids a second pass over psutil.
            show_tcp: Include TCP endpoints.
            show_udp: Include UDP endpoints.
            allow_netstat_fallback: Permit the ``netstat`` last resort.
        """
        self.process_lookup = process_lookup
        self.show_tcp = show_tcp
        self.show_udp = show_udp
        self.allow_netstat_fallback = allow_netstat_fallback
        self.source: Source = "psutil"
        self.degraded_reason: str | None = None

    # -- public API -------------------------------------------------------- #

    def collect(self) -> list[ConnectionInfo]:
        """Return every TCP/UDP endpoint the current token can see."""
        rows = self._collect_psutil()
        if rows is None:
            rows = self._collect_windows_api()
        if rows is None and self.allow_netstat_fallback:
            rows = self._collect_netstat()
        if rows is None:
            self.source = "unavailable"
            return []
        return [row for row in rows if self._protocol_enabled(row.protocol)]

    # -- layer 1: psutil --------------------------------------------------- #

    def _collect_psutil(self) -> list[ConnectionInfo] | None:
        try:
            raw = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError) as exc:
            logger.info("psutil.net_connections denied (%s), using the Windows API directly", exc)
            self.degraded_reason = "psutil was denied access to the connection table"
            return None
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("psutil.net_connections failed: %s", exc)
            self.degraded_reason = str(exc)
            return None

        self.source = "psutil"
        self.degraded_reason = None
        rows: list[ConnectionInfo] = []
        for conn in raw:
            if not conn.laddr:
                continue
            protocol = "TCP" if conn.type == socket.SOCK_STREAM else "UDP"
            remote_address = conn.raddr.ip if conn.raddr else None
            remote_port = conn.raddr.port if conn.raddr else None
            state = conn.status if conn.status and conn.status != psutil.CONN_NONE else None
            rows.append(
                self._build(
                    protocol=protocol,
                    family=int(conn.family),
                    local_address=conn.laddr.ip,
                    local_port=conn.laddr.port,
                    remote_address=remote_address,
                    remote_port=remote_port,
                    state=state,
                    pid=conn.pid,
                )
            )
        return rows

    # -- layer 2: Windows API ---------------------------------------------- #

    def _collect_windows_api(self) -> list[ConnectionInfo] | None:
        try:
            raw = list(windows.tcp_connections()) + list(windows.udp_connections())
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Windows connection table query failed: %s", exc)
            return None
        if not raw:
            return None
        self.source = "windows-api"
        return [
            self._build(
                protocol=row.protocol,
                family=row.family,
                local_address=row.local_address,
                local_port=row.local_port,
                remote_address=row.remote_address,
                remote_port=row.remote_port,
                state=row.state,
                pid=row.pid,
            )
            for row in raw
        ]

    # -- layer 3: netstat -------------------------------------------------- #

    def _collect_netstat(self) -> list[ConnectionInfo] | None:
        """Parse ``netstat -ano``.

        Only reached when both API paths failed.  ``netstat`` output is
        localised in its headers but the table rows themselves are not, so the
        parser keys off the protocol token and ignores anything unexpected.
        """
        try:
            completed = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("netstat fallback failed: %s", exc)
            return None
        if completed.returncode != 0:
            logger.warning("netstat exited with %s", completed.returncode)
            return None

        self.source = "netstat"
        self.degraded_reason = self.degraded_reason or "Falling back to netstat output"
        rows: list[ConnectionInfo] = []
        for line in completed.stdout.splitlines():
            parsed = _parse_netstat_line(line)
            if parsed is None:
                continue
            protocol, local, remote, state, pid = parsed
            local_address, local_port = local
            remote_address, remote_port = remote if remote else (None, None)
            rows.append(
                self._build(
                    protocol=protocol,
                    family=int(socket.AF_INET6) if ":" in local_address else int(socket.AF_INET),
                    local_address=local_address,
                    local_port=local_port,
                    remote_address=remote_address,
                    remote_port=remote_port,
                    state=state,
                    pid=pid,
                )
            )
        return rows

    # -- helpers ----------------------------------------------------------- #

    def _protocol_enabled(self, protocol: str) -> bool:
        if protocol == "TCP":
            return self.show_tcp
        if protocol == "UDP":
            return self.show_udp
        return True

    def _build(
        self,
        protocol: str,
        family: int,
        local_address: str,
        local_port: int,
        remote_address: str | None,
        remote_port: int | None,
        state: str | None,
        pid: int | None,
    ) -> ConnectionInfo:
        """Attach process details to a raw endpoint row."""
        name: str | None = None
        create_time: float | None = None
        executable: str | None = None
        # PID 0 is the idle process: the connection table uses it for sockets
        # with no live owner (TIME_WAIT leftovers), so it is treated as unknown.
        if pid and pid > 0 and self.process_lookup is not None:
            found = self.process_lookup(pid)
            if found is not None:
                name, create_time, executable = found
        return ConnectionInfo(
            protocol=protocol,
            family=_FAMILY_NAMES.get(family, "IPv4"),
            local_address=local_address,
            local_port=local_port,
            remote_address=remote_address,
            remote_port=remote_port,
            state=state,
            pid=pid if pid and pid > 0 else None,
            process_name=name,
            process_create_time=create_time,
            executable=executable,
        )


def _split_endpoint(token: str) -> tuple[str, int] | None:
    """Split ``127.0.0.1:8080`` or ``[::1]:8080`` into host and port."""
    if not token or token == "*:*":
        return None
    host, separator, port = token.rpartition(":")
    if not separator:
        return None
    host = host.strip("[]")
    if port in ("*", ""):
        return (host, 0)
    try:
        return (host, int(port))
    except ValueError:
        return None


def _parse_netstat_line(
    line: str,
) -> tuple[str, tuple[str, int], tuple[str, int] | None, str | None, int | None] | None:
    """Parse one ``netstat -ano`` row, returning ``None`` for anything else."""
    parts = line.split()
    if len(parts) < 4:
        return None
    protocol = parts[0].upper()
    if protocol not in _NETSTAT_PROTOCOLS:
        return None
    local = _split_endpoint(parts[1])
    if local is None:
        return None
    remote = _split_endpoint(parts[2]) if len(parts) > 2 else None

    if protocol.startswith("TCP"):
        # PROTO LOCAL REMOTE STATE PID
        if len(parts) < 5:
            return None
        raw_state = parts[3].upper()
        state = _NETSTAT_STATES.get(raw_state, raw_state)
        pid_token = parts[4]
    else:
        # PROTO LOCAL REMOTE PID  (UDP rows have no state column)
        state = None
        pid_token = parts[3]
        remote = None

    try:
        pid: int | None = int(pid_token)
    except ValueError:
        pid = None
    return ("TCP" if protocol.startswith("TCP") else "UDP", local, remote, state, pid)
