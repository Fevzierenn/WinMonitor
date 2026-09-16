"""Application state.

A :class:`Snapshot` is one consistent reading of the machine.  :class:`AppState`
holds the newest snapshot plus the view state (search text, sort order,
selection) and derives what each screen should show.  Keeping the derivation
here rather than in the widgets means the CLI and the TUI answer questions the
same way, and that the answers can be tested without a terminal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config.settings import Settings
from ..models import ConnectionInfo, PortInfo, ProcessInfo, ProcessSort, SystemInfo
from ..services import network_service, process_service

__all__ = ["AppState", "Snapshot", "View"]

#: The screens the UI can show, in navigation order.
View = str

VIEWS: tuple[str, ...] = ("dashboard", "processes", "ports", "connections")


@dataclass(slots=True)
class Snapshot:
    """One consistent reading of processes, connections and system metrics."""

    processes: list[ProcessInfo] = field(default_factory=list)
    connections: list[ConnectionInfo] = field(default_factory=list)
    system: SystemInfo = field(default_factory=SystemInfo)
    taken_at: float = field(default_factory=time.time)
    duration: float = 0.0
    network_source: str = "psutil"
    process_source: str = "windows-api"
    degraded_reason: str | None = None

    @property
    def listening_ports(self) -> list[PortInfo]:
        """Server sockets only, deduplicated."""
        return network_service.to_ports(self.connections, listening_only=True)

    @property
    def all_ports(self) -> list[PortInfo]:
        """Every local endpoint, including outbound connections."""
        return network_service.to_ports(self.connections, listening_only=False)

    @property
    def age(self) -> float:
        """Seconds since the snapshot was taken."""
        return max(0.0, time.time() - self.taken_at)


@dataclass(slots=True)
class AppState:
    """Newest snapshot plus everything the user has chosen in the UI."""

    settings: Settings
    snapshot: Snapshot = field(default_factory=Snapshot)
    view: View = "dashboard"
    process_query: str = ""
    port_query: str = ""
    connection_query: str = ""
    sort_key: ProcessSort = ProcessSort.CPU
    sort_descending: bool = True
    show_system_processes: bool = True
    listening_only: bool = True
    selected_pid: int | None = None
    selected_port: int | None = None
    paused: bool = False
    status_message: str = ""

    @classmethod
    def from_settings(cls, settings: Settings) -> AppState:
        """Build the initial state from configuration."""
        return cls(
            settings=settings,
            sort_key=ProcessSort(settings.sort_by),
            sort_descending=settings.sort_descending,
            show_system_processes=settings.show_system_processes,
            listening_only=settings.show_listening_only,
        )

    # -- derived views ----------------------------------------------------- #

    def visible_processes(self) -> list[ProcessInfo]:
        """Processes after search, the system toggle and sorting."""
        filtered = process_service.filter_processes(
            self.snapshot.processes,
            query=self.process_query,
            show_system=self.show_system_processes,
        )
        return process_service.sort_processes(filtered, self.sort_key, self.sort_descending)

    def visible_ports(self) -> list[PortInfo]:
        """Ports after the listening toggle and search, sorted by port number."""
        ports = self.snapshot.listening_ports if self.listening_only else self.snapshot.all_ports
        return network_service.sort_ports(network_service.filter_ports(ports, self.port_query))

    def visible_connections(self) -> list[ConnectionInfo]:
        """Connections after search, newest-looking first (by local port)."""
        connections = network_service.filter_connections(
            self.snapshot.connections, self.connection_query
        )
        return sorted(
            connections,
            key=lambda connection: (
                connection.protocol,
                connection.local_port,
                connection.remote_port or 0,
            ),
        )

    def developer_ports(self) -> list[PortInfo]:
        """Active well known development ports."""
        return network_service.developer_ports(self.snapshot.listening_ports)

    def selected_process(self) -> ProcessInfo | None:
        """The process the cursor is on, if it still exists."""
        if self.selected_pid is None:
            return None
        return process_service.find_by_pid(self.snapshot.processes, self.selected_pid)

    def ports_for_selected(self) -> list[PortInfo]:
        """Ports held by the selected process."""
        if self.selected_pid is None:
            return []
        return network_service.ports_for_pid(self.snapshot.connections, self.selected_pid)

    # -- mutations --------------------------------------------------------- #

    def cycle_sort(self) -> ProcessSort:
        """Advance to the next sort key, wrapping around."""
        keys = list(ProcessSort)
        index = keys.index(self.sort_key)
        self.sort_key = keys[(index + 1) % len(keys)]
        return self.sort_key

    def set_sort(self, key: ProcessSort | str) -> None:
        """Set the sort key, flipping direction when it is already active."""
        try:
            new_key = ProcessSort(key)
        except ValueError:
            return
        if new_key == self.sort_key:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_key = new_key
            self.sort_descending = new_key not in (ProcessSort.NAME, ProcessSort.PID)

    def query_for_view(self) -> str:
        """The active search text for the current view."""
        return {
            "processes": self.process_query,
            "ports": self.port_query,
            "connections": self.connection_query,
        }.get(self.view, "")

    def set_query(self, query: str) -> None:
        """Set the search text of the current view."""
        if self.view == "processes":
            self.process_query = query
        elif self.view == "ports":
            self.port_query = query
        elif self.view == "connections":
            self.connection_query = query
