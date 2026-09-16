"""The live process table."""

from __future__ import annotations

from ..app.state import AppState
from ..models import ProcessInfo
from ..utils.formatting import format_bytes, truncate
from .table_pane import Column, TablePane, cell
from .widgets import severity_style

__all__ = ["ProcessesPane"]


class ProcessesPane(TablePane):
    """Every running process, sorted and filtered by the application state."""

    COLUMNS = (
        Column("pid", "PID", 8),
        Column("name", "PROCESS", 26),
        Column("cpu", "CPU", 8),
        Column("memory", "MEMORY", 11),
        Column("mempct", "MEM%", 7),
        Column("threads", "THR", 6),
        Column("handles", "HANDLES", 9),
        Column("user", "USER", 16),
        Column("status", "STATUS", 11),
        Column("uptime", "UPTIME", 13),
        Column("ports", "PORTS", 18),
    )

    def update_state(self, state: AppState) -> None:
        """Rebuild the table from the newest snapshot."""
        processes = state.visible_processes()
        self.rebuild([(str(process.pid), _row(process)) for process in processes])

        total = len(state.snapshot.processes)
        parts = [f"{len(processes)} of {total} processes"]
        parts.append(
            f"sorted by {state.sort_key.value} {'desc' if state.sort_descending else 'asc'}"
        )
        if state.process_query:
            parts.append(f"filter: {state.process_query!r}")
        if not state.show_system_processes:
            parts.append("system processes hidden")
        self.set_caption("   |   ".join(parts))


def _row(process: ProcessInfo) -> tuple:
    """Render one process as table cells."""
    name_style = "bold red" if process.is_critical else ("yellow" if process.is_sensitive else "")
    status_style = "yellow" if process.status == "suspended" else "dim"
    return (
        cell(process.pid, "dim"),
        cell(truncate(process.name, 25), name_style),
        cell(f"{process.cpu_percent:.1f}%", severity_style(process.cpu_percent)),
        cell(format_bytes(process.memory_bytes)),
        cell(f"{process.memory_percent:.1f}%", "dim"),
        cell(process.thread_count if process.thread_count is not None else "-", "dim"),
        cell(process.handle_count if process.handle_count is not None else "-", "dim"),
        cell(truncate(process.username_display, 15), "dim"),
        cell(process.status, status_style),
        cell(process.uptime),
        cell(process.port_summary, "cyan" if process.port_summary else ""),
    )
