"""The connection table: active TCP sessions and bound UDP sockets."""

from __future__ import annotations

from ..app.state import AppState
from ..models import ConnectionInfo
from ..utils.formatting import truncate
from .table_pane import Column, TablePane, cell

__all__ = ["ConnectionsPane"]

_STATE_STYLES = {
    "ESTABLISHED": "green",
    "LISTENING": "green",
    "BOUND": "dim",
    "TIME_WAIT": "yellow",
    "CLOSE_WAIT": "yellow",
    "SYN_SENT": "yellow",
    "SYN_RECV": "yellow",
    "FIN_WAIT1": "yellow",
    "FIN_WAIT2": "yellow",
    "CLOSING": "yellow",
    "LAST_ACK": "yellow",
    "CLOSE": "dim",
}


class ConnectionsPane(TablePane):
    """Every socket, local and remote endpoints side by side."""

    COLUMNS = (
        Column("proto", "PROTO", 7),
        Column("family", "FAMILY", 8),
        Column("local", "LOCAL ENDPOINT", 28),
        Column("remote", "REMOTE ENDPOINT", 28),
        Column("state", "STATE", 13),
        Column("pid", "PID", 8),
        Column("process", "PROCESS", 24),
        Column("uptime", "PROC UPTIME", 13),
    )

    def update_state(self, state: AppState) -> None:
        """Rebuild the table from the newest snapshot."""
        connections = state.visible_connections()
        self.rebuild([(connection.key, _row(connection)) for connection in connections])

        established = sum(1 for connection in connections if connection.is_active)
        parts = [
            f"{len(connections)} sockets",
            f"{established} active",
        ]
        if state.connection_query:
            parts.append(f"filter: {state.connection_query!r}")
        self.set_caption("   |   ".join(parts))


def _row(connection: ConnectionInfo) -> tuple:
    """Render one connection as table cells."""
    display_state = connection.display_state
    return (
        cell(connection.protocol, "dim"),
        cell(connection.family, "dim"),
        cell(truncate(connection.local_endpoint, 27)),
        cell(truncate(connection.remote_endpoint, 27), "dim"),
        cell(display_state, _STATE_STYLES.get(display_state, "")),
        cell(connection.pid if connection.pid is not None else "-", "dim"),
        cell(truncate(connection.process_name or "unknown", 23)),
        cell(connection.process_uptime, "dim"),
    )
