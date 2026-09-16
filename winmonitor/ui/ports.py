"""The port table: which local endpoints are in use and who owns them."""

from __future__ import annotations

from ..app.state import AppState
from ..models import PortInfo
from ..utils.formatting import truncate
from .table_pane import Column, TablePane, cell

__all__ = ["PortsPane"]

#: States that mean the socket is serving rather than winding down.
_HEALTHY_STATES = {"LISTENING", "BOUND", "ESTABLISHED"}


class PortsPane(TablePane):
    """Listening ports, or every local endpoint when the filter is relaxed."""

    COLUMNS = (
        Column("proto", "PROTO", 7),
        Column("address", "LOCAL ADDRESS", 26),
        Column("port", "PORT", 7),
        Column("state", "STATE", 12),
        Column("service", "SERVICE", 20),
        Column("pid", "PID", 8),
        Column("process", "PROCESS", 24),
        Column("started", "STARTED", 21),
        Column("uptime", "UPTIME", 13),
    )

    def update_state(self, state: AppState) -> None:
        """Rebuild the table from the newest snapshot."""
        ports = state.visible_ports()
        self.rebuild([(port.key, _row(port)) for port in ports])

        scope = "listening ports" if state.listening_only else "local endpoints"
        parts = [f"{len(ports)} {scope}"]
        developer = len(state.developer_ports())
        if developer:
            parts.append(f"{developer} development port(s) active")
        if state.port_query:
            parts.append(f"filter: {state.port_query!r}")
        parts.append("press l to toggle listening-only")
        self.set_caption("   |   ".join(parts))


def _row(port: PortInfo) -> tuple:
    """Render one port as table cells."""
    state_style = "green" if port.display_state in _HEALTHY_STATES else "yellow"
    return (
        cell(port.protocol, "dim"),
        cell(truncate(port.local_address, 25)),
        cell(port.local_port, "bold cyan" if port.is_developer_port else "bold"),
        cell(port.display_state, state_style),
        cell(truncate(port.service or "", 19), "dim"),
        cell(port.pid if port.pid is not None else "-", "dim"),
        cell(truncate(port.process_name or "unknown", 23)),
        cell(port.process_started, "dim"),
        cell(port.process_uptime, "dim"),
    )
