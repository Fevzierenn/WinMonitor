"""The dashboard: system totals plus the top consumers and developer ports."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

from ..app.state import AppState
from ..models import ProcessInfo
from ..services import process_service
from ..utils.formatting import format_bytes, truncate
from .widgets import MeterBar, StatTile, severity_style

__all__ = ["DashboardPane"]


class DashboardPane(VerticalScroll):
    """Single screen overview of the machine."""

    def compose(self) -> ComposeResult:
        with Container(classes="panel", id="system-panel"):
            yield Static("SYSTEM", classes="panel-title")
            with Horizontal(classes="meters"):
                with Vertical(classes="meter-column"):
                    yield MeterBar("CPU", id="meter-cpu")
                    yield MeterBar("MEMORY", id="meter-memory")
                    yield MeterBar("DISK", id="meter-disk")
                with Vertical(classes="tiles"):
                    yield StatTile("NETWORK RX", id="tile-rx")
                    yield StatTile("NETWORK TX", id="tile-tx")
                    yield StatTile("PROCESSES", id="tile-processes")
                    yield StatTile("PORTS", id="tile-ports")
                    yield StatTile("CONNECTIONS", id="tile-connections")
                    yield StatTile("SYSTEM UPTIME", id="tile-uptime")
                    yield StatTile("ADMINISTRATOR", id="tile-admin")

        with Horizontal(classes="columns"):
            with Container(classes="panel half"):
                yield Static("TOP PROCESSES BY CPU", classes="panel-title")
                yield Static("", id="top-cpu")
            with Container(classes="panel half"):
                yield Static("TOP PROCESSES BY MEMORY", classes="panel-title")
                yield Static("", id="top-memory")

        with Container(classes="panel", id="dev-ports-panel"):
            yield Static("DEVELOPMENT PORTS", classes="panel-title")
            yield Static("", id="dev-ports")

    def update_state(self, state: AppState) -> None:
        """Re-render every tile from the newest snapshot."""
        system = state.snapshot.system

        self.query_one("#meter-cpu", MeterBar).update_value(
            system.cpu.percent, system.cpu.core_count_display
        )
        self.query_one("#meter-memory", MeterBar).update_value(
            system.memory.percent, system.memory.display
        )
        disk = system.primary_disk
        self.query_one("#meter-disk", MeterBar).update_value(
            disk.percent if disk else None,
            f"{disk.mountpoint}  {disk.display}" if disk else "no volume readable",
        )

        self.query_one("#tile-rx", StatTile).update_value(system.network.receive_display)
        self.query_one("#tile-tx", StatTile).update_value(system.network.send_display)
        self.query_one("#tile-processes", StatTile).update_value(str(system.process_count))
        self.query_one("#tile-ports", StatTile).update_value(str(system.listening_port_count))
        self.query_one("#tile-connections", StatTile).update_value(str(system.connection_count))
        self.query_one("#tile-uptime", StatTile).update_value(system.uptime)
        self.query_one("#tile-admin", StatTile).update_value(
            system.admin_display, "green" if system.is_admin else "yellow"
        )

        processes = state.snapshot.processes
        self.query_one("#top-cpu", Static).update(
            _top_table(process_service.top_by_cpu(processes, 6), metric="cpu")
        )
        self.query_one("#top-memory", Static).update(
            _top_table(process_service.top_by_memory(processes, 6), metric="memory")
        )
        self.query_one("#dev-ports", Static).update(_developer_table(state))


def _top_table(processes: list[ProcessInfo], metric: str) -> Text:
    """Render the top-consumers list."""
    if not processes:
        return Text("Collecting...", style="dim")
    text = Text()
    for process in processes:
        text.append(f"{truncate(process.name, 22):<23}", style="bold")
        if metric == "cpu":
            text.append(f"{process.cpu_percent:>6.1f}%", style=severity_style(process.cpu_percent))
        else:
            text.append(f"{format_bytes(process.memory_bytes):>10}")
            text.append(f"  {process.memory_percent:>5.1f}%", style="dim")
        text.append(f"   PID {process.pid}\n", style="dim")
    return text


def _developer_table(state: AppState) -> Text:
    """Render the well known development ports that are currently active."""
    ports = state.developer_ports()
    if not ports:
        return Text(
            "No development ports are in use right now.\n"
            "Watching 3000, 4200, 5173, 5432, 6379, 8080, 9092, 27017 and 20 more.",
            style="dim",
        )
    text = Text()
    text.append(
        f"{'PORT':<8}{'PROCESS':<24}{'PID':>8}  {'STATUS':<12}{'SERVICE':<22}UPTIME\n", style="dim"
    )
    for port in ports:
        text.append(f"{port.local_port:<8}", style="bold cyan")
        text.append(f"{truncate(port.process_name or 'unknown', 23):<24}")
        text.append(f"{port.pid or 0:>8}  ", style="dim")
        text.append(
            f"{port.display_state:<12}",
            style="green" if port.display_state == "LISTENING" else "yellow",
        )
        text.append(f"{truncate(port.service or '', 21):<22}", style="dim")
        text.append(f"{port.process_uptime}\n", style="dim")
    return text
